"""Interior defensive line style profiles for sim and tick-level play behavior.

These are football behavior traits, not hidden personality traits. They let
interior defenders with similar ratings play differently as penetrators,
pocket collapsers, nose anchors, two-gappers, or stunt rushers.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class IDLBehaviorProfile:
    label: str
    getoff_timing: float
    penetration_burst: float
    power_collapse: float
    double_team_anchor: float
    gap_control: float
    block_shed_timing: float
    stunt_timing: float
    rush_counter_plan: float
    finish_skill: float
    rush_discipline: float
    notes: str = ""

    def as_dict(self) -> dict[str, float | str]:
        return {
            "label": self.label,
            "getoff_timing": round(self.getoff_timing, 1),
            "penetration_burst": round(self.penetration_burst, 1),
            "power_collapse": round(self.power_collapse, 1),
            "double_team_anchor": round(self.double_team_anchor, 1),
            "gap_control": round(self.gap_control, 1),
            "block_shed_timing": round(self.block_shed_timing, 1),
            "stunt_timing": round(self.stunt_timing, 1),
            "rush_counter_plan": round(self.rush_counter_plan, 1),
            "finish_skill": round(self.finish_skill, 1),
            "rush_discipline": round(self.rush_discipline, 1),
            "notes": self.notes,
        }


IDL_BEHAVIOR_FIELDS = (
    "getoff_timing",
    "penetration_burst",
    "power_collapse",
    "double_team_anchor",
    "gap_control",
    "block_shed_timing",
    "stunt_timing",
    "rush_counter_plan",
    "finish_skill",
    "rush_discipline",
)


IDL_POSITIONS = {"IDL", "DT", "NT"}


def profile(
    label: str,
    getoff: int,
    penetration: int,
    power: int,
    anchor: int,
    gap: int,
    shed: int,
    stunt: int,
    counter: int,
    finish: int,
    discipline: int,
    notes: str = "",
) -> IDLBehaviorProfile:
    return IDLBehaviorProfile(
        label=label,
        getoff_timing=getoff,
        penetration_burst=penetration,
        power_collapse=power,
        double_team_anchor=anchor,
        gap_control=gap,
        block_shed_timing=shed,
        stunt_timing=stunt,
        rush_counter_plan=counter,
        finish_skill=finish,
        rush_discipline=discipline,
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


def profile_from_mapping(values: dict[str, Any]) -> IDLBehaviorProfile:
    return IDLBehaviorProfile(
        label=str(values.get("label") or "Stored IDL Profile"),
        getoff_timing=float(values.get("getoff_timing", 50)),
        penetration_burst=float(values.get("penetration_burst", 50)),
        power_collapse=float(values.get("power_collapse", 50)),
        double_team_anchor=float(values.get("double_team_anchor", 50)),
        gap_control=float(values.get("gap_control", 50)),
        block_shed_timing=float(values.get("block_shed_timing", 50)),
        stunt_timing=float(values.get("stunt_timing", 50)),
        rush_counter_plan=float(values.get("rush_counter_plan", 50)),
        finish_skill=float(values.get("finish_skill", 50)),
        rush_discipline=float(values.get("rush_discipline", 50)),
        notes=str(values.get("notes") or ""),
    )


def profile_to_db_tuple(player_or_prospect_id: int, season: int | None, profile: IDLBehaviorProfile, source: str):
    base = [
        int(player_or_prospect_id),
    ]
    if season is not None:
        base.append(int(season))
    base.extend(
        [
            profile.label,
            int(round(profile.getoff_timing)),
            int(round(profile.penetration_burst)),
            int(round(profile.power_collapse)),
            int(round(profile.double_team_anchor)),
            int(round(profile.gap_control)),
            int(round(profile.block_shed_timing)),
            int(round(profile.stunt_timing)),
            int(round(profile.rush_counter_plan)),
            int(round(profile.finish_skill)),
            int(round(profile.rush_discipline)),
            source,
            profile.notes,
        ]
    )
    return tuple(base)


def ensure_player_idl_behavior_schema(con) -> None:
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS player_idl_behavior_profiles (
            player_id INTEGER NOT NULL REFERENCES players(player_id) ON DELETE CASCADE,
            season INTEGER NOT NULL,
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
            source TEXT NOT NULL DEFAULT 'manual',
            notes TEXT,
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (player_id, season)
        );

        CREATE INDEX IF NOT EXISTS idx_player_idl_behavior_profiles_season
            ON player_idl_behavior_profiles(season, label);
        """
    )


def player_idl_behavior_table_exists(con) -> bool:
    row = con.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type = 'table'
          AND name = 'player_idl_behavior_profiles'
        """
    ).fetchone()
    return bool(row)


IDL_STYLE_OVERRIDES: dict[str, IDLBehaviorProfile] = {
    "alim mcneill": profile("Explosive Young Three-Tech", 78, 80, 82, 82, 78, 78, 76, 78, 76, 78),
    "arik armstead": profile("Long Power Interior", 70, 72, 84, 86, 84, 82, 76, 80, 76, 82),
    "ashawn robinson": profile("Veteran Two-Gap Anchor", 58, 58, 78, 88, 84, 78, 68, 70, 64, 82),
    "bj hill": profile("Steady Power Interior", 68, 68, 80, 82, 80, 78, 72, 76, 72, 80),
    "braden fiske": profile("High-Energy Penetrator", 80, 82, 80, 76, 74, 76, 78, 76, 74, 74),
    "bryan bresee": profile("Young Power Athlete", 78, 78, 80, 78, 74, 76, 76, 74, 74, 72),
    "byron murphy": profile("Compact Gap Penetrator", 76, 80, 82, 80, 78, 78, 76, 76, 74, 74),
    "calais campbell": profile("Veteran Length Interior", 62, 64, 82, 84, 84, 82, 74, 82, 74, 88),
    "caleb banks": profile(
        "Young Power Interior",
        78,
        78,
        86,
        86,
        78,
        82,
        76,
        74,
        74,
        72,
        "Power and size profile with more run value than refined rush finish.",
    ),
    "calijah kancey": profile("Explosive Gap Shooter", 88, 90, 82, 72, 76, 78, 82, 78, 78, 74),
    "cameron heyward": profile("Veteran Power Two-Gapper", 70, 72, 90, 90, 90, 86, 78, 88, 80, 90),
    "charles omenihu": profile("Inside-Outside Rusher", 82, 82, 78, 70, 72, 72, 78, 76, 74, 72),
    "chris jones": profile("Elite Interior Rush Engine", 86, 88, 96, 88, 88, 88, 90, 96, 92, 88),
    "christian barmore": profile("Power Rush Interior", 78, 80, 88, 84, 80, 80, 78, 82, 78, 76),
    "christian wilkins": profile("Complete Interior Disruptor", 82, 82, 88, 86, 92, 86, 84, 88, 82, 88),
    "daquan jones": profile("Veteran Nose Anchor", 66, 66, 82, 88, 86, 80, 74, 80, 72, 86),
    "daron payne": profile("Power Pocket Collapser", 76, 78, 88, 90, 88, 86, 80, 84, 80, 82),
    "davon godchaux": profile("Nose Run Stabilizer", 58, 58, 78, 88, 82, 76, 68, 72, 66, 82),
    "davon hamilton": profile("Nose Anchor", 58, 58, 80, 88, 82, 78, 70, 74, 68, 82),
    "deforest buckner": profile("Long Interior Rusher", 82, 84, 94, 88, 92, 90, 88, 92, 90, 88),
    "derrick brown": profile("Dominant Run Anchor", 70, 72, 88, 94, 90, 88, 78, 82, 78, 86),
    "dexter lawrence": profile("Elite Nose Disruptor", 70, 72, 94, 96, 92, 90, 82, 86, 82, 88),
    "dj jones": profile("Low-Center Nose Anchor", 58, 58, 76, 84, 82, 76, 68, 72, 66, 80),
    "dj reader": profile("Veteran Nose Anchor", 66, 66, 82, 88, 86, 80, 74, 78, 72, 84),
    "domonique orange": profile(
        "Nose Anchor",
        58,
        56,
        72,
        86,
        80,
        76,
        60,
        62,
        58,
        72,
        "Run-stuffing nose tackle profile; should absorb blocks more than collect sacks.",
    ),
    "ed oliver": profile("Explosive Undersized Interior", 84, 86, 84, 78, 78, 78, 80, 82, 78, 76),
    "gervon dexter": profile("Young Power Interior", 76, 76, 82, 84, 78, 80, 74, 74, 72, 74),
    "grady jarrett": profile("Veteran Gap Disruptor", 82, 84, 84, 78, 86, 82, 84, 88, 82, 86),
    "grover stewart": profile("Run-Game Anchor", 68, 70, 84, 92, 90, 86, 76, 80, 76, 86),
    "harrison phillips": profile("Steady Run Interior", 62, 62, 78, 84, 82, 76, 70, 74, 68, 82),
    "jalen carter": profile("Explosive Power Interior", 84, 90, 92, 88, 84, 86, 86, 84, 86, 78),
    "jalen redmond": profile(
        "Interior Penetrator",
        84,
        84,
        82,
        76,
        76,
        78,
        80,
        78,
        78,
        76,
        "Quick interior rusher profile with useful disruption and moderate anchor.",
    ),
    "jarran reed": profile("Veteran Run Interior", 60, 62, 78, 84, 82, 76, 70, 76, 68, 82),
    "javon hargrave": profile("Veteran Interior Rusher", 78, 82, 84, 80, 82, 80, 82, 84, 78, 80),
    "jerzhan newton": profile("Young Gap Penetrator", 80, 82, 80, 78, 76, 78, 78, 76, 76, 74),
    "jeffery simmons": profile("Complete Power Interior", 84, 84, 92, 92, 92, 90, 88, 90, 90, 88),
    "john franklinmyers": profile("Inside-Outside Power Rusher", 82, 80, 84, 80, 80, 80, 78, 80, 78, 78),
    "jonathan allen": profile("Veteran Interior Technician", 76, 78, 84, 84, 84, 82, 80, 84, 78, 84),
    "jordan davis": profile("Massive Nose Anchor", 68, 68, 90, 96, 88, 86, 76, 78, 74, 78),
    "kenneth grant": profile("Massive Rookie Nose", 68, 68, 84, 92, 84, 82, 72, 72, 72, 74),
    "kenny clark": profile("Veteran Power Nose", 70, 72, 86, 90, 86, 84, 78, 82, 76, 86),
    "khalen saunders": profile("Compact Power Interior", 76, 76, 80, 82, 80, 80, 76, 78, 76, 78),
    "kobie turner": profile("Quick Interior Worker", 74, 76, 78, 76, 76, 76, 74, 76, 74, 76),
    "leonard williams": profile("Long Power Interior", 70, 72, 86, 88, 86, 84, 78, 84, 78, 84),
    "mason graham": profile("Rookie Power Technician", 76, 78, 84, 88, 82, 84, 76, 76, 74, 78),
    "milton williams": profile("Explosive Interior Rusher", 82, 84, 84, 78, 78, 78, 82, 82, 80, 76),
    "nnamdi madubuike": profile("Interior Rush Finisher", 78, 82, 88, 84, 82, 80, 80, 82, 80, 78),
    "osa odighizuwa": profile("Quick Power Three-Tech", 80, 82, 86, 82, 80, 80, 80, 82, 78, 78),
    "poona ford": profile("Leverage Nose Anchor", 58, 58, 80, 86, 82, 78, 70, 74, 68, 80),
    "quinnen williams": profile("Explosive Power Interior", 80, 84, 92, 88, 88, 86, 84, 88, 84, 84),
    "sheldon rankins": profile("Veteran Gap Rusher", 72, 74, 78, 76, 78, 74, 76, 78, 72, 78),
    "taki taimani": profile("Depth Nose Anchor", 56, 54, 70, 84, 78, 74, 58, 62, 56, 72),
    "teair tart": profile("Power Run Interior", 58, 60, 80, 86, 82, 78, 70, 74, 70, 78),
    "travis jones": profile("Young Nose Anchor", 60, 60, 80, 88, 80, 78, 70, 72, 68, 76),
    "tvondre sweat": profile("Massive Two-Gap Nose", 54, 52, 84, 94, 84, 80, 66, 70, 64, 74),
    "vita vea": profile("Rare Power Nose", 64, 66, 92, 96, 90, 88, 78, 82, 76, 84),
    "walter nolen": profile("Rookie Gap Penetrator", 78, 82, 82, 80, 76, 78, 76, 74, 72, 72),
    "zach allen": profile("Power-Gap Disruptor", 72, 70, 80, 82, 88, 78, 78, 82, 76, 84),
    "zach sieler": profile("Complete Interior Worker", 78, 78, 84, 84, 88, 84, 82, 84, 80, 86),
}


class RatingProfileSource:
    def __init__(self, ratings: dict[str, int | float], name: str = "Generated IDL", position: str = "IDL") -> None:
        self.name = name
        self.position = position
        self.ratings = ratings

    def rating(self, key: str, default: float = 50.0) -> float:
        return float(self.ratings.get(key, default))


def infer_profile(interior: PlayerSnapshot) -> IDLBehaviorProfile:
    position = str(getattr(interior, "position", "IDL")).upper()
    quickness = average([interior.rating("speed"), interior.rating("acceleration"), interior.rating("agility")])
    getoff = clamp_rating(
        interior.rating("acceleration") * 0.28
        + interior.rating("power_rush") * 0.20
        + interior.rating("finesse_rush") * 0.16
        + interior.rating("processing_speed") * 0.16
        + interior.rating("discipline") * 0.10
        + interior.rating("speed") * 0.10
    )
    penetration = clamp_rating(
        interior.rating("acceleration") * 0.28
        + interior.rating("finesse_rush") * 0.24
        + interior.rating("power_rush") * 0.18
        + interior.rating("agility") * 0.14
        + interior.rating("rush_plan") * 0.10
        + interior.rating("strength") * 0.06
    )
    power = clamp_rating(
        interior.rating("power_rush") * 0.38
        + interior.rating("strength") * 0.28
        + interior.rating("block_shedding") * 0.12
        + interior.rating("balance") * 0.12
        + interior.rating("double_team_takeon") * 0.10
    )
    anchor = clamp_rating(
        interior.rating("double_team_takeon") * 0.38
        + interior.rating("strength") * 0.26
        + interior.rating("gap_integrity") * 0.18
        + interior.rating("balance") * 0.10
        + interior.rating("block_shedding") * 0.08
    )
    gap = clamp_rating(
        interior.rating("gap_integrity") * 0.42
        + interior.rating("run_diagnostics") * 0.22
        + interior.rating("play_recognition") * 0.14
        + interior.rating("discipline") * 0.12
        + interior.rating("strength") * 0.10
    )
    shed = clamp_rating(
        interior.rating("block_shedding") * 0.38
        + interior.rating("strength") * 0.20
        + interior.rating("power_rush") * 0.14
        + interior.rating("processing_speed") * 0.12
        + interior.rating("tackle_wrap") * 0.08
        + interior.rating("balance") * 0.08
    )
    stunt = clamp_rating(
        interior.rating("stunt_execution") * 0.42
        + interior.rating("processing_speed") * 0.22
        + interior.rating("rush_plan") * 0.14
        + interior.rating("acceleration") * 0.12
        + interior.rating("discipline") * 0.10
    )
    counter = clamp_rating(
        interior.rating("rush_plan") * 0.36
        + interior.rating("power_rush") * 0.20
        + interior.rating("finesse_rush") * 0.18
        + interior.rating("processing_speed") * 0.14
        + interior.rating("play_recognition") * 0.12
    )
    finish = clamp_rating(
        interior.rating("sack_finish") * 0.42
        + interior.rating("tackle_wrap") * 0.14
        + interior.rating("power_rush") * 0.14
        + interior.rating("rush_plan") * 0.12
        + interior.rating("acceleration") * 0.10
        + interior.rating("composure") * 0.08
    )
    discipline = clamp_rating(
        interior.rating("discipline") * 0.44
        + interior.rating("composure") * 0.20
        + interior.rating("consistency") * 0.16
        + interior.rating("processing_speed") * 0.10
        + interior.rating("play_recognition") * 0.10
    )

    if position == "NT":
        anchor = clamp_rating(anchor + 5)
        gap = clamp_rating(gap + 4)
        finish = clamp_rating(finish - 4)

    if anchor >= 84 and gap >= 80 and quickness <= 64:
        label = "Inferred Nose Anchor"
    elif penetration >= 80 and getoff >= 76:
        label = "Inferred Gap Penetrator"
    elif power >= 82 and counter >= 80:
        label = "Inferred Interior Rusher"
    elif anchor >= 82 and gap >= 82:
        label = "Inferred Two-Gapper"
    elif gap >= 78 and shed >= 78:
        label = "Inferred Run Defender"
    else:
        label = "Inferred Balanced Interior"

    return IDLBehaviorProfile(
        label=label,
        getoff_timing=getoff,
        penetration_burst=penetration,
        power_collapse=power,
        double_team_anchor=anchor,
        gap_control=gap,
        block_shed_timing=shed,
        stunt_timing=stunt,
        rush_counter_plan=counter,
        finish_skill=finish,
        rush_discipline=discipline,
        notes="Inferred from current interior defensive line ratings.",
    )


def with_deltas(base: IDLBehaviorProfile, *, label: str, notes: str, **deltas: float) -> IDLBehaviorProfile:
    values = base.as_dict()
    for field in IDL_BEHAVIOR_FIELDS:
        values[field] = clamp(float(values[field]) + float(deltas.get(field, 0.0)), 1, 99)
    values["label"] = label
    values["notes"] = notes
    return profile_from_mapping(values)


def generated_idl_behavior_profile(
    archetype: str,
    ratings: dict[str, int | float],
    *,
    position: str = "IDL",
) -> IDLBehaviorProfile:
    base = infer_profile(RatingProfileSource(ratings, position=position))
    archetype = str(archetype or "").strip()
    if archetype == "Interior rusher":
        return with_deltas(
            base,
            label="Generated Interior Rusher",
            notes="Generated from draft IDL archetype and true ratings.",
            getoff_timing=6,
            penetration_burst=8,
            power_collapse=6,
            double_team_anchor=-4,
            gap_control=-3,
            block_shed_timing=1,
            stunt_timing=7,
            rush_counter_plan=8,
            finish_skill=8,
            rush_discipline=-2,
        )
    if archetype == "Nose tackle":
        return with_deltas(
            base,
            label="Generated Nose Tackle",
            notes="Generated from draft IDL archetype and true ratings.",
            getoff_timing=-6,
            penetration_burst=-8,
            power_collapse=8,
            double_team_anchor=16,
            gap_control=10,
            block_shed_timing=6,
            stunt_timing=-4,
            rush_counter_plan=-4,
            finish_skill=-8,
            rush_discipline=5,
        )
    if archetype == "Gap penetrator":
        return with_deltas(
            base,
            label="Generated Gap Penetrator",
            notes="Generated from draft IDL archetype and true ratings.",
            getoff_timing=10,
            penetration_burst=14,
            power_collapse=-2,
            double_team_anchor=-10,
            gap_control=-5,
            block_shed_timing=2,
            stunt_timing=5,
            rush_counter_plan=3,
            finish_skill=4,
            rush_discipline=-3,
        )
    if archetype == "Two-gapper":
        return with_deltas(
            base,
            label="Generated Two-Gapper",
            notes="Generated from draft IDL archetype and true ratings.",
            getoff_timing=-4,
            penetration_burst=-6,
            power_collapse=6,
            double_team_anchor=12,
            gap_control=14,
            block_shed_timing=8,
            stunt_timing=-2,
            rush_counter_plan=-2,
            finish_skill=-6,
            rush_discipline=8,
        )
    return with_deltas(
        base,
        label="Generated Balanced IDL",
        notes="Generated from draft IDL true ratings.",
    )


def metadata_profile(interior) -> IDLBehaviorProfile | None:
    metadata = getattr(interior, "metadata", None) or {}
    stored = metadata.get("idl_behavior_profile")
    if isinstance(stored, IDLBehaviorProfile):
        return stored
    if isinstance(stored, dict):
        return profile_from_mapping(stored)
    return None


def metadata_source(interior) -> str | None:
    metadata = getattr(interior, "metadata", None) or {}
    source = metadata.get("idl_behavior_source")
    return str(source) if source is not None else None


def idl_behavior_source(interior) -> str:
    stored = metadata_profile(interior)
    stored_source = metadata_source(interior)
    if stored and stored_source != "idl_behavior_named_seed":
        return "stored"
    if normalize_name(interior.name) in IDL_STYLE_OVERRIDES:
        return "named"
    if stored:
        return "stored"
    return "inferred"


def idl_behavior_profile(interior: PlayerSnapshot) -> IDLBehaviorProfile:
    stored = metadata_profile(interior)
    stored_source = metadata_source(interior)
    if stored and stored_source != "idl_behavior_named_seed":
        return stored
    named = IDL_STYLE_OVERRIDES.get(normalize_name(interior.name))
    if named:
        return named
    if stored:
        return stored
    return infer_profile(interior)
