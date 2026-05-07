"""Edge defender style profiles for sim and tick-level play behavior.

These are football behavior traits, not hidden personality traits. They let
edge defenders with similar ratings win in different ways: speed arcs, power
collapses, stunt timing, contain discipline, and finishing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class EdgeBehaviorProfile:
    label: str
    getoff_timing: float
    speed_arc: float
    power_collapse: float
    counter_plan: float
    stunt_timing: float
    contain_discipline: float
    run_squeeze: float
    backside_pursuit: float
    finish_skill: float
    rush_discipline: float
    notes: str = ""

    def as_dict(self) -> dict[str, float | str]:
        return {
            "label": self.label,
            "getoff_timing": round(self.getoff_timing, 1),
            "speed_arc": round(self.speed_arc, 1),
            "power_collapse": round(self.power_collapse, 1),
            "counter_plan": round(self.counter_plan, 1),
            "stunt_timing": round(self.stunt_timing, 1),
            "contain_discipline": round(self.contain_discipline, 1),
            "run_squeeze": round(self.run_squeeze, 1),
            "backside_pursuit": round(self.backside_pursuit, 1),
            "finish_skill": round(self.finish_skill, 1),
            "rush_discipline": round(self.rush_discipline, 1),
            "notes": self.notes,
        }


EDGE_BEHAVIOR_FIELDS = (
    "getoff_timing",
    "speed_arc",
    "power_collapse",
    "counter_plan",
    "stunt_timing",
    "contain_discipline",
    "run_squeeze",
    "backside_pursuit",
    "finish_skill",
    "rush_discipline",
)


EDGE_POSITIONS = {"EDGE", "OLB", "DE"}


def profile(
    label: str,
    getoff: int,
    speed_arc: int,
    power: int,
    counter: int,
    stunt: int,
    contain: int,
    run: int,
    pursuit: int,
    finish: int,
    discipline: int,
    notes: str = "",
) -> EdgeBehaviorProfile:
    return EdgeBehaviorProfile(
        label=label,
        getoff_timing=getoff,
        speed_arc=speed_arc,
        power_collapse=power,
        counter_plan=counter,
        stunt_timing=stunt,
        contain_discipline=contain,
        run_squeeze=run,
        backside_pursuit=pursuit,
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


def profile_from_mapping(values: dict[str, Any]) -> EdgeBehaviorProfile:
    return EdgeBehaviorProfile(
        label=str(values.get("label") or "Stored Edge Profile"),
        getoff_timing=float(values.get("getoff_timing", 50)),
        speed_arc=float(values.get("speed_arc", 50)),
        power_collapse=float(values.get("power_collapse", 50)),
        counter_plan=float(values.get("counter_plan", 50)),
        stunt_timing=float(values.get("stunt_timing", 50)),
        contain_discipline=float(values.get("contain_discipline", 50)),
        run_squeeze=float(values.get("run_squeeze", 50)),
        backside_pursuit=float(values.get("backside_pursuit", 50)),
        finish_skill=float(values.get("finish_skill", 50)),
        rush_discipline=float(values.get("rush_discipline", 50)),
        notes=str(values.get("notes") or ""),
    )


def profile_to_db_tuple(player_or_prospect_id: int, season: int | None, profile: EdgeBehaviorProfile, source: str):
    base = [
        int(player_or_prospect_id),
    ]
    if season is not None:
        base.append(int(season))
    base.extend(
        [
            profile.label,
            int(round(profile.getoff_timing)),
            int(round(profile.speed_arc)),
            int(round(profile.power_collapse)),
            int(round(profile.counter_plan)),
            int(round(profile.stunt_timing)),
            int(round(profile.contain_discipline)),
            int(round(profile.run_squeeze)),
            int(round(profile.backside_pursuit)),
            int(round(profile.finish_skill)),
            int(round(profile.rush_discipline)),
            source,
            profile.notes,
        ]
    )
    return tuple(base)


def ensure_player_edge_behavior_schema(con) -> None:
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS player_edge_behavior_profiles (
            player_id INTEGER NOT NULL REFERENCES players(player_id) ON DELETE CASCADE,
            season INTEGER NOT NULL,
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
            source TEXT NOT NULL DEFAULT 'manual',
            notes TEXT,
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (player_id, season)
        );

        CREATE INDEX IF NOT EXISTS idx_player_edge_behavior_profiles_season
            ON player_edge_behavior_profiles(season, label);
        """
    )


def player_edge_behavior_table_exists(con) -> bool:
    row = con.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type = 'table'
          AND name = 'player_edge_behavior_profiles'
        """
    ).fetchone()
    return bool(row)


EDGE_STYLE_OVERRIDES: dict[str, EdgeBehaviorProfile] = {
    "abdul carter": profile("Explosive Rookie Edge", 86, 88, 76, 74, 76, 72, 72, 86, 76, 72),
    "aidan hutchinson": profile("Every-Down Power Technician", 86, 84, 88, 88, 88, 84, 86, 84, 86, 88),
    "alex highsmith": profile("Polished Counter Edge", 80, 80, 76, 84, 80, 78, 76, 78, 78, 82),
    "andrew van ginkel": profile(
        "Smart Versatile Edge",
        80,
        80,
        78,
        84,
        84,
        84,
        82,
        84,
        80,
        86,
        "Versatile edge/linebacker profile with disciplined contain and timing value.",
    ),
    "arnold ebiketie": profile("Burst Edge Rotator", 80, 82, 74, 74, 76, 72, 72, 80, 74, 74),
    "bj ojulari": profile("Young Speed Edge", 79, 81, 70, 72, 73, 70, 69, 78, 72, 72),
    "boye mafe": profile("Ascending Speed Edge", 82, 84, 74, 76, 76, 74, 73, 82, 75, 76),
    "bradley chubb": profile("Veteran Power Edge", 76, 77, 82, 80, 80, 80, 80, 76, 78, 82),
    "brian burns": profile("Wide-Arc Speed Rusher", 88, 92, 80, 86, 84, 78, 76, 88, 86, 82),
    "carl granderson": profile("Steady Balanced Edge", 76, 76, 78, 78, 76, 78, 78, 76, 76, 78),
    "chase young": profile("Long Burst Edge", 82, 84, 80, 78, 78, 76, 76, 82, 78, 76),
    "chop robinson": profile("Explosive Arc Rusher", 92, 94, 78, 78, 80, 76, 74, 88, 82, 76),
    "danielle hunter": profile("Veteran Rush Finisher", 82, 84, 82, 90, 86, 82, 80, 82, 86, 86),
    "dallas turner": profile(
        "Explosive Young Rusher",
        84,
        86,
        76,
        76,
        78,
        72,
        72,
        82,
        78,
        74,
        "Athletic edge profile with high ceiling as the rush plan develops.",
    ),
    "dayo odeyingbo": profile("Power Edge Rotator", 74, 74, 80, 78, 78, 76, 78, 74, 76, 76),
    "demarcus lawrence": profile("Veteran Run-Squeeze Edge", 72, 74, 82, 82, 78, 88, 90, 76, 76, 88),
    "felix anudikeuzomah": profile("Young Rush Technician", 80, 80, 76, 76, 78, 72, 72, 78, 76, 76),
    "george karlaftis": profile("Power-Plan Edge", 78, 78, 86, 84, 82, 82, 84, 78, 82, 84),
    "greg rousseau": profile("Length-and-Power Edge", 78, 80, 82, 82, 80, 82, 84, 80, 80, 82),
    "haason reddick": profile("Speed Finish Specialist", 84, 86, 78, 82, 80, 78, 76, 82, 84, 78),
    "harold landry": profile("Veteran Speed Technician", 78, 80, 76, 82, 80, 76, 74, 78, 78, 82),
    "jadeveon clowney": profile("Veteran Power Edge", 82, 80, 88, 86, 84, 88, 90, 82, 82, 84),
    "jaelan phillips": profile("Explosive Balanced Edge", 82, 84, 76, 76, 76, 74, 74, 82, 76, 76),
    "jalon walker": profile("Hybrid Rookie Edge", 82, 82, 74, 74, 76, 78, 76, 84, 74, 76),
    "james pearce": profile("Long Speed Prospect", 84, 86, 72, 72, 74, 70, 69, 82, 74, 72),
    "jared verse": profile("Physical Rookie Edge", 84, 84, 82, 82, 82, 78, 80, 82, 82, 78),
    "jermaine johnson": profile("Balanced Young Edge", 78, 78, 78, 76, 76, 78, 78, 78, 76, 76),
    "joey bosa": profile("Veteran Power Technician", 80, 80, 88, 88, 86, 86, 86, 78, 82, 82),
    "jonathan greenard": profile("Timed Rush Finisher", 80, 80, 78, 82, 82, 78, 78, 80, 80, 82),
    "jonathon cooper": profile("High-Motor Edge", 76, 74, 72, 76, 76, 78, 78, 78, 72, 80),
    "josh hinesallen": profile("Complete Power Edge", 84, 84, 90, 90, 88, 88, 88, 84, 88, 88),
    "josh sweat": profile("Long Speed-to-Power Edge", 78, 80, 82, 80, 80, 76, 76, 78, 78, 78),
    "josh uche": profile("Situational Speed Rusher", 86, 88, 74, 80, 80, 74, 72, 82, 80, 76),
    "kayvon thibodeaux": profile("Young Speed-Power Edge", 84, 86, 78, 76, 78, 76, 74, 82, 76, 74),
    "khalil mack": profile("Veteran Power Master", 78, 78, 92, 90, 88, 90, 92, 78, 86, 90),
    "kwity paye": profile("Run-Strong Power Edge", 78, 78, 82, 76, 76, 82, 84, 78, 76, 76),
    "laiatu latu": profile("Rookie Rush Technician", 84, 84, 82, 84, 82, 78, 78, 82, 82, 80),
    "leonard floyd": profile("Veteran Arc Edge", 76, 78, 76, 80, 78, 80, 78, 78, 76, 82),
    "lukas van ness": profile("Power Developmental Edge", 76, 76, 82, 74, 74, 78, 80, 76, 74, 74),
    "malcolm koonce": profile("Burst Power Edge", 80, 82, 80, 76, 76, 78, 78, 80, 76, 74),
    "maxx crosby": profile("Relentless Complete Edge", 94, 94, 94, 96, 94, 92, 94, 94, 94, 92),
    "micah parsons": profile("Explosive Matchup Wrecker", 94, 96, 88, 90, 88, 86, 84, 94, 92, 84),
    "montez sweat": profile("Long Speed-to-Power Edge", 84, 86, 84, 84, 84, 80, 80, 82, 84, 82),
    "myles garrett": profile("Elite Power-Athlete Edge", 90, 90, 96, 94, 92, 88, 90, 88, 92, 90),
    "myles murphy": profile("Power Development Edge", 76, 76, 78, 74, 74, 74, 76, 76, 73, 74),
    "nick bosa": profile("Elite Technical Edge", 84, 88, 94, 96, 92, 90, 90, 84, 90, 92),
    "nick herbig": profile("Light Speed Edge", 80, 82, 70, 74, 74, 72, 70, 80, 72, 72),
    "nik bonitto": profile("Arc-and-Counter Edge", 78, 82, 70, 82, 78, 80, 78, 78, 72, 84),
    "nolan smith": profile("Explosive Speed Edge", 88, 90, 72, 76, 76, 76, 74, 88, 76, 76),
    "odafe oweh": profile("Explosive Power Athlete", 84, 86, 80, 76, 78, 74, 74, 82, 76, 74),
    "rashan gary": profile("Power-Athlete Edge", 82, 82, 86, 82, 82, 80, 82, 80, 82, 80),
    "t.j. watt": profile("Elite Turnover Edge", 88, 88, 90, 94, 90, 88, 88, 90, 94, 90),
    "tj watt": profile("Elite Turnover Edge", 88, 88, 90, 94, 90, 88, 88, 90, 94, 90),
    "travon walker": profile("Prototype Power Athlete", 86, 84, 90, 84, 82, 88, 90, 84, 86, 82),
    "trey hendrickson": profile("Veteran Sack Finisher", 78, 82, 82, 86, 84, 78, 78, 78, 86, 82),
    "tuli tuipulotu": profile("Power-Edge Finisher", 80, 80, 86, 82, 82, 84, 86, 80, 82, 82),
    "uchenna nwosu": profile("Balanced Veteran Edge", 78, 78, 76, 78, 78, 78, 76, 78, 76, 78),
    "will anderson": profile("Complete Young Edge", 88, 90, 86, 90, 88, 84, 84, 88, 88, 86),
    "will mcdonald": profile("Speed Rush Specialist", 84, 86, 70, 74, 76, 70, 68, 82, 72, 72),
    "yaya diaby": profile("Long Speed Edge", 80, 80, 76, 74, 76, 74, 74, 78, 74, 74),
    "zaven collins": profile("Hybrid Power Edge", 76, 74, 78, 76, 76, 80, 80, 78, 75, 78),
}


class RatingProfileSource:
    def __init__(self, ratings: dict[str, int | float], name: str = "Generated Edge", position: str = "EDGE") -> None:
        self.name = name
        self.position = position
        self.ratings = ratings

    def rating(self, key: str, default: float = 50.0) -> float:
        return float(self.ratings.get(key, default))


def infer_profile(edge: PlayerSnapshot) -> EdgeBehaviorProfile:
    position = str(getattr(edge, "position", "EDGE")).upper()
    processing = average([edge.rating("processing_speed"), edge.rating("play_recognition")])
    coverage = average([edge.rating("man_coverage"), edge.rating("zone_coverage"), edge.rating("coverage_communication")])
    getoff = clamp_rating(
        edge.rating("acceleration") * 0.34
        + edge.rating("speed_rush") * 0.28
        + edge.rating("speed") * 0.20
        + edge.rating("processing_speed") * 0.10
        + edge.rating("discipline") * 0.08
    )
    speed_arc = clamp_rating(
        edge.rating("speed_rush") * 0.38
        + edge.rating("acceleration") * 0.24
        + edge.rating("agility") * 0.18
        + edge.rating("finesse_rush") * 0.14
        + edge.rating("balance") * 0.06
    )
    power = clamp_rating(
        edge.rating("power_rush") * 0.40
        + edge.rating("strength") * 0.24
        + edge.rating("block_shedding") * 0.14
        + edge.rating("balance") * 0.12
        + edge.rating("double_team_takeon") * 0.10
    )
    counter = clamp_rating(
        edge.rating("rush_plan") * 0.40
        + edge.rating("finesse_rush") * 0.18
        + edge.rating("power_rush") * 0.14
        + edge.rating("processing_speed") * 0.14
        + edge.rating("play_recognition") * 0.14
    )
    stunt = clamp_rating(
        edge.rating("stunt_execution") * 0.42
        + edge.rating("processing_speed") * 0.22
        + edge.rating("acceleration") * 0.14
        + edge.rating("rush_plan") * 0.12
        + edge.rating("discipline") * 0.10
    )
    contain = clamp_rating(
        edge.rating("edge_contain") * 0.42
        + edge.rating("gap_integrity") * 0.24
        + edge.rating("discipline") * 0.14
        + edge.rating("pursuit_angle") * 0.10
        + edge.rating("play_recognition") * 0.10
    )
    run = clamp_rating(
        edge.rating("edge_contain") * 0.26
        + edge.rating("gap_integrity") * 0.24
        + edge.rating("block_shedding") * 0.20
        + edge.rating("strength") * 0.16
        + edge.rating("tackle_wrap") * 0.14
    )
    pursuit = clamp_rating(
        edge.rating("pursuit_angle") * 0.30
        + edge.rating("speed") * 0.22
        + edge.rating("acceleration") * 0.18
        + edge.rating("play_recognition") * 0.14
        + edge.rating("stamina") * 0.10
        + edge.rating("agility") * 0.06
    )
    finish = clamp_rating(
        edge.rating("sack_finish") * 0.44
        + edge.rating("tackle_wrap") * 0.16
        + edge.rating("acceleration") * 0.14
        + edge.rating("rush_plan") * 0.14
        + edge.rating("composure") * 0.12
    )
    discipline = clamp_rating(
        edge.rating("discipline") * 0.44
        + edge.rating("composure") * 0.20
        + edge.rating("consistency") * 0.16
        + edge.rating("processing_speed") * 0.12
        + edge.rating("play_recognition") * 0.08
    )

    if position == "OLB" and coverage >= average([speed_arc, power, counter]) - 2:
        label = "Inferred Hybrid Edge"
        contain = clamp_rating(contain + 3)
        pursuit = clamp_rating(pursuit + 3)
        discipline = clamp_rating(discipline + 2)
    elif speed_arc >= power + 8 and getoff >= 76:
        label = "Inferred Speed Rusher"
    elif power >= speed_arc + 7 and run >= 76:
        label = "Inferred Power Edge"
    elif contain >= 78 and run >= 78:
        label = "Inferred Run-Setting Edge"
    elif counter >= 80 or finish >= 80:
        label = "Inferred Rush Technician"
    elif processing >= 78:
        label = "Inferred Veteran Edge"
    else:
        label = "Inferred Balanced Edge"

    return EdgeBehaviorProfile(
        label=label,
        getoff_timing=getoff,
        speed_arc=speed_arc,
        power_collapse=power,
        counter_plan=counter,
        stunt_timing=stunt,
        contain_discipline=contain,
        run_squeeze=run,
        backside_pursuit=pursuit,
        finish_skill=finish,
        rush_discipline=discipline,
        notes="Inferred from current edge defender ratings.",
    )


def with_deltas(base: EdgeBehaviorProfile, *, label: str, notes: str, **deltas: float) -> EdgeBehaviorProfile:
    values = base.as_dict()
    for field in EDGE_BEHAVIOR_FIELDS:
        values[field] = clamp(float(values[field]) + float(deltas.get(field, 0.0)), 1, 99)
    values["label"] = label
    values["notes"] = notes
    return profile_from_mapping(values)


def generated_edge_behavior_profile(
    archetype: str,
    ratings: dict[str, int | float],
    *,
    position: str = "EDGE",
) -> EdgeBehaviorProfile:
    base = infer_profile(RatingProfileSource(ratings, position=position))
    archetype = str(archetype or "").strip()
    if archetype == "Speed rusher":
        return with_deltas(
            base,
            label="Generated Speed Rusher",
            notes="Generated from draft EDGE archetype and true ratings.",
            getoff_timing=12,
            speed_arc=14,
            power_collapse=-7,
            counter_plan=2,
            stunt_timing=4,
            contain_discipline=-4,
            run_squeeze=-7,
            backside_pursuit=8,
            finish_skill=6,
            rush_discipline=-3,
        )
    if archetype == "Power edge":
        return with_deltas(
            base,
            label="Generated Power Edge",
            notes="Generated from draft EDGE archetype and true ratings.",
            getoff_timing=2,
            speed_arc=-8,
            power_collapse=14,
            counter_plan=6,
            stunt_timing=2,
            contain_discipline=5,
            run_squeeze=10,
            backside_pursuit=-3,
            finish_skill=7,
            rush_discipline=2,
        )
    if archetype == "Hybrid linebacker":
        return with_deltas(
            base,
            label="Generated Hybrid Edge",
            notes="Generated from draft OLB/EDGE archetype and true ratings.",
            getoff_timing=4,
            speed_arc=3,
            power_collapse=-8,
            counter_plan=2,
            stunt_timing=7,
            contain_discipline=9,
            run_squeeze=4,
            backside_pursuit=12,
            finish_skill=-3,
            rush_discipline=7,
        )
    if archetype == "Run-setting edge":
        return with_deltas(
            base,
            label="Generated Run-Setting Edge",
            notes="Generated from draft EDGE archetype and true ratings.",
            getoff_timing=-2,
            speed_arc=-8,
            power_collapse=8,
            counter_plan=0,
            stunt_timing=1,
            contain_discipline=14,
            run_squeeze=14,
            backside_pursuit=5,
            finish_skill=-4,
            rush_discipline=8,
        )
    return with_deltas(
        base,
        label="Generated Balanced Edge",
        notes="Generated from draft edge true ratings.",
    )


def metadata_profile(edge) -> EdgeBehaviorProfile | None:
    metadata = getattr(edge, "metadata", None) or {}
    stored = metadata.get("edge_behavior_profile")
    if isinstance(stored, EdgeBehaviorProfile):
        return stored
    if isinstance(stored, dict):
        return profile_from_mapping(stored)
    return None


def metadata_source(edge) -> str | None:
    metadata = getattr(edge, "metadata", None) or {}
    source = metadata.get("edge_behavior_source")
    return str(source) if source is not None else None


def edge_behavior_source(edge) -> str:
    stored = metadata_profile(edge)
    stored_source = metadata_source(edge)
    if stored and stored_source != "edge_behavior_named_seed":
        return "stored"
    if normalize_name(edge.name) in EDGE_STYLE_OVERRIDES:
        return "named"
    if stored:
        return "stored"
    return "inferred"


def edge_behavior_profile(edge: PlayerSnapshot) -> EdgeBehaviorProfile:
    stored = metadata_profile(edge)
    stored_source = metadata_source(edge)
    if stored and stored_source != "edge_behavior_named_seed":
        return stored
    named = EDGE_STYLE_OVERRIDES.get(normalize_name(edge.name))
    if named:
        return named
    if stored:
        return stored
    return infer_profile(edge)
