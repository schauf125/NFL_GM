"""Quarterback style profiles for tick-level play behavior.

These are football behavior traits, not hidden personality traits. They give
the tick resolver a way to make QBs with similar ratings play differently.
All values are 0-100, where 50 is neutral.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class QBBehaviorProfile:
    label: str
    rhythm: float
    pocket_discipline: float
    pocket_drift: float
    checkdown_willingness: float
    deep_aggression: float
    pressure_escape: float
    broken_play_creation: float
    scramble_trigger: float
    sack_risk: float
    throwaway_discipline: float
    notes: str = ""

    def as_dict(self) -> dict[str, float | str]:
        return {
            "label": self.label,
            "rhythm": round(self.rhythm, 1),
            "pocket_discipline": round(self.pocket_discipline, 1),
            "pocket_drift": round(self.pocket_drift, 1),
            "checkdown_willingness": round(self.checkdown_willingness, 1),
            "deep_aggression": round(self.deep_aggression, 1),
            "pressure_escape": round(self.pressure_escape, 1),
            "broken_play_creation": round(self.broken_play_creation, 1),
            "scramble_trigger": round(self.scramble_trigger, 1),
            "sack_risk": round(self.sack_risk, 1),
            "throwaway_discipline": round(self.throwaway_discipline, 1),
            "notes": self.notes,
        }


QB_BEHAVIOR_FIELDS = (
    "rhythm",
    "pocket_discipline",
    "pocket_drift",
    "checkdown_willingness",
    "deep_aggression",
    "pressure_escape",
    "broken_play_creation",
    "scramble_trigger",
    "sack_risk",
    "throwaway_discipline",
)


def profile(
    label: str,
    rhythm: int,
    pocket_discipline: int,
    pocket_drift: int,
    checkdown: int,
    deep_aggression: int,
    pressure_escape: int,
    broken_play: int,
    scramble: int,
    sack_risk: int,
    throwaway: int,
    notes: str = "",
) -> QBBehaviorProfile:
    return QBBehaviorProfile(
        label=label,
        rhythm=rhythm,
        pocket_discipline=pocket_discipline,
        pocket_drift=pocket_drift,
        checkdown_willingness=checkdown,
        deep_aggression=deep_aggression,
        pressure_escape=pressure_escape,
        broken_play_creation=broken_play,
        scramble_trigger=scramble,
        sack_risk=sack_risk,
        throwaway_discipline=throwaway,
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


def profile_from_mapping(values: dict[str, Any]) -> QBBehaviorProfile:
    return QBBehaviorProfile(
        label=str(values.get("label") or "Stored QB Profile"),
        rhythm=float(values.get("rhythm", 50)),
        pocket_discipline=float(values.get("pocket_discipline", 50)),
        pocket_drift=float(values.get("pocket_drift", 50)),
        checkdown_willingness=float(values.get("checkdown_willingness", 50)),
        deep_aggression=float(values.get("deep_aggression", 50)),
        pressure_escape=float(values.get("pressure_escape", 50)),
        broken_play_creation=float(values.get("broken_play_creation", 50)),
        scramble_trigger=float(values.get("scramble_trigger", 50)),
        sack_risk=float(values.get("sack_risk", 50)),
        throwaway_discipline=float(values.get("throwaway_discipline", 50)),
        notes=str(values.get("notes") or ""),
    )


def profile_to_db_tuple(player_or_prospect_id: int, season: int | None, profile: QBBehaviorProfile, source: str):
    base = [
        int(player_or_prospect_id),
    ]
    if season is not None:
        base.append(int(season))
    base.extend(
        [
            profile.label,
            int(round(profile.rhythm)),
            int(round(profile.pocket_discipline)),
            int(round(profile.pocket_drift)),
            int(round(profile.checkdown_willingness)),
            int(round(profile.deep_aggression)),
            int(round(profile.pressure_escape)),
            int(round(profile.broken_play_creation)),
            int(round(profile.scramble_trigger)),
            int(round(profile.sack_risk)),
            int(round(profile.throwaway_discipline)),
            source,
            profile.notes,
        ]
    )
    return tuple(base)


def ensure_player_qb_behavior_schema(con) -> None:
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS player_qb_behavior_profiles (
            player_id INTEGER NOT NULL REFERENCES players(player_id) ON DELETE CASCADE,
            season INTEGER NOT NULL,
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
            source TEXT NOT NULL DEFAULT 'manual',
            notes TEXT,
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (player_id, season)
        );

        CREATE INDEX IF NOT EXISTS idx_player_qb_behavior_profiles_season
            ON player_qb_behavior_profiles(season, label);
        """
    )


def player_qb_behavior_table_exists(con) -> bool:
    row = con.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type = 'table'
          AND name = 'player_qb_behavior_profiles'
        """
    ).fetchone()
    return bool(row)


QB_STYLE_OVERRIDES: dict[str, QBBehaviorProfile] = {
    "aaron rodgers": profile("Veteran Anticipator", 78, 82, 32, 60, 63, 42, 56, 24, 38, 74),
    "anthony richardson": profile("Volatile Power Runner", 38, 34, 78, 31, 83, 86, 84, 88, 91, 30),
    "baker mayfield": profile("Aggressive Rhythm Competitor", 72, 68, 58, 47, 74, 58, 70, 52, 58, 55),
    "bo nix": profile("Rhythm Escape Manager", 82, 76, 44, 74, 46, 70, 62, 54, 34, 76),
    "brock purdy": profile("Timing Distributor", 78, 74, 40, 64, 50, 56, 58, 36, 46, 64),
    "bryce young": profile("Pocket Resetter", 62, 58, 64, 58, 49, 68, 67, 56, 66, 55),
    "caleb williams": profile(
        "Backfield Creator",
        48,
        36,
        92,
        38,
        78,
        84,
        90,
        88,
        90,
        34,
        "Often extends plays behind the line, creating both explosives and deep negative sacks.",
    ),
    "cam ward": profile("Aggressive Creator", 48, 42, 78, 34, 84, 78, 86, 78, 78, 38),
    "cj stroud": profile("Pocket Shot Taker", 72, 70, 50, 48, 76, 58, 68, 42, 56, 58),
    "dak prescott": profile("Structured Aggressor", 78, 78, 42, 60, 66, 58, 62, 36, 42, 70),
    "daniel jones": profile("Read-Option Mover", 58, 54, 64, 52, 50, 76, 66, 76, 66, 50),
    "deshaun watson": profile("Rusty Extender", 45, 38, 74, 34, 70, 70, 72, 68, 82, 36),
    "drake maye": profile("Big-Frame Playmaker", 64, 58, 66, 46, 78, 78, 84, 66, 66, 50),
    "geno smith": profile("Aggressive Veteran", 68, 64, 50, 44, 76, 55, 64, 38, 62, 54),
    "jalen hurts": profile("Power Dual Threat", 64, 66, 58, 50, 64, 88, 82, 86, 48, 64),
    "jalen milroe": profile("Explosive Run Threat", 34, 32, 84, 28, 76, 88, 84, 90, 88, 30),
    "jared goff": profile("Pure Rhythm Pocket", 90, 86, 24, 70, 40, 28, 36, 12, 44, 78),
    "jaxson dart": profile("Young Vertical Mover", 44, 40, 72, 36, 78, 78, 78, 72, 76, 38),
    "jameis winston": profile("Volatile Bomb Thrower", 52, 44, 62, 24, 96, 48, 78, 28, 70, 30),
    "jayden daniels": profile("Gliding Dual Threat", 56, 58, 70, 44, 66, 94, 88, 88, 58, 54),
    "joe burrow": profile("Rhythm Surgeon", 94, 90, 28, 68, 62, 42, 56, 18, 48, 80),
    "joe flacco": profile("Stationary Vertical Veteran", 60, 64, 26, 42, 86, 22, 38, 10, 68, 48),
    "joe milton": profile("Raw Cannon", 30, 30, 78, 22, 92, 72, 78, 66, 86, 24),
    "jordan love": profile("Second-Reaction Thrower", 68, 62, 60, 46, 78, 62, 76, 48, 58, 54),
    "josh allen": profile("Power Chaos Creator", 62, 56, 72, 38, 84, 92, 94, 82, 66, 48),
    "josh dobbs": profile("Mobile Spot Starter", 56, 54, 58, 54, 42, 70, 58, 68, 58, 54),
    "justin fields": profile("Hold-And-Run Threat", 36, 34, 88, 26, 76, 90, 82, 92, 92, 28),
    "justin herbert": profile("Big-Arm Structure Plus", 76, 78, 44, 54, 78, 70, 70, 48, 44, 66),
    "kirk cousins": profile("Late-Career Timing Veteran", 74, 78, 18, 70, 40, 18, 24, 6, 66, 68),
    "kyler murray": profile(
        "Compact Escape Artist",
        54,
        50,
        76,
        44,
        70,
        92,
        84,
        80,
        70,
        44,
        "Short-area quickness and escape ability create off-schedule plays, but structure can loosen.",
    ),
    "lamar jackson": profile("MVP Run-Pass Stressor", 66, 66, 66, 50, 66, 99, 94, 96, 42, 58),
    "malik willis": profile("Athletic Improviser", 40, 38, 80, 34, 70, 86, 76, 84, 82, 34),
    "marcus mariota": profile("Mobile Distributor", 62, 66, 46, 66, 40, 74, 56, 58, 42, 68),
    "mason rudolph": profile("Conservative Pocket Backup", 66, 70, 34, 64, 46, 34, 38, 14, 46, 66),
    "matthew stafford": profile("Late-Career Shot Taker", 78, 76, 34, 42, 78, 28, 56, 10, 68, 48),
    "michael penix": profile("Vertical Pocket Rookie", 58, 58, 38, 38, 82, 38, 54, 18, 58, 46),
    "patrick mahomes": profile("Controlled Improviser", 78, 72, 66, 50, 78, 82, 98, 54, 38, 66),
    "quinn ewers": profile("Quick-Game Arm Talent", 56, 56, 52, 46, 70, 58, 62, 34, 58, 48),
    "russell wilson": profile("Deep Reset Scrambler", 40, 42, 72, 24, 86, 58, 70, 42, 88, 30),
    "sam darnold": profile("Aggressive Play-Action Passer", 66, 62, 52, 42, 76, 52, 64, 34, 62, 48),
    "shedeur sanders": profile("Developing Pocket Rookie", 58, 60, 34, 54, 46, 38, 46, 20, 68, 48),
    "spencer rattler": profile("Volatile Off-Schedule Backup", 42, 36, 74, 28, 78, 58, 72, 52, 82, 28),
    "trevor lawrence": profile("Long-Strider Shot Creator", 68, 62, 58, 46, 78, 74, 76, 58, 62, 52),
    "tua tagovailoa": profile("Anticipation Distributor", 80, 72, 30, 68, 48, 34, 42, 10, 52, 64),
    "tyler huntley": profile("Mobile Backup Rhythm", 54, 54, 58, 54, 42, 78, 62, 70, 54, 58),
    "tyrod taylor": profile("Careful Mobile Veteran", 58, 66, 44, 66, 42, 76, 58, 62, 38, 78),
    "will levis": profile("Cannon Volatility", 34, 32, 76, 22, 90, 70, 76, 62, 92, 24),
    "zach wilson": profile("Backyard Backup", 36, 32, 78, 24, 76, 62, 72, 56, 88, 24),
}


QB_STYLE_OVERRIDES.update(
    {
        "aidan oconnell": profile("Stationary Backup Distributor", 62, 66, 34, 60, 42, 30, 38, 14, 48, 64),
        "andy dalton": profile("Quick Veteran Reserve", 66, 72, 30, 68, 42, 30, 38, 14, 42, 72),
        "bailey zappe": profile("Rhythm Spot Backup", 58, 62, 36, 62, 44, 36, 42, 20, 52, 62),
        "brandon allen": profile("Conservative Reserve", 58, 62, 34, 64, 42, 34, 38, 16, 52, 64),
        "brett rypien": profile("Checkdown Reserve", 60, 66, 30, 70, 36, 30, 34, 12, 46, 72),
        "cade klubnik": profile("Young Rhythm Mover", 54, 54, 56, 50, 58, 62, 60, 56, 64, 50),
        "carson beck": profile("Developmental Pocket Arm", 50, 54, 42, 50, 66, 42, 54, 24, 62, 48),
        "carson wentz": profile("Big-Arm Volatile Veteran", 44, 40, 66, 30, 82, 58, 70, 46, 86, 30),
        "case keenum": profile("Emergency Rhythm Veteran", 64, 70, 30, 68, 38, 30, 38, 12, 42, 72),
        "clayton tune": profile("Mobile Development Backup", 48, 46, 62, 46, 52, 66, 58, 60, 66, 44),
        "cooper rush": profile("Low-Variance Spot Starter", 68, 72, 26, 70, 34, 24, 32, 8, 38, 78),
        "davis mills": profile("Tall Pocket Reserve", 60, 62, 34, 54, 58, 34, 44, 16, 54, 60),
        "derek carr": profile("Veteran Timing Thrower", 72, 76, 30, 62, 56, 32, 46, 14, 46, 70),
        "desmond ridder": profile("Mobile Conservative Backup", 52, 54, 58, 56, 42, 66, 54, 62, 60, 56),
        "dillon gabriel": profile("Quick-Game Rookie Lefty", 62, 60, 48, 66, 44, 58, 54, 48, 48, 66),
        "dorian thompsonrobinson": profile("Athletic Emergency Creator", 38, 34, 78, 30, 62, 82, 70, 82, 84, 32),
        "drew lock": profile("Vertical Backup Gambler", 42, 38, 66, 28, 84, 58, 72, 50, 80, 30),
        "easton stick": profile("Mobile System Backup", 52, 56, 54, 58, 38, 66, 54, 58, 52, 62),
        "fernando mendoza": profile("Toolsy Rookie Projection", 44, 42, 62, 38, 82, 62, 76, 56, 76, 38),
        "gardner minshew": profile("Improvising Spot Starter", 56, 54, 64, 48, 62, 62, 68, 52, 66, 50),
        "hendon hooker": profile("Developmental Vertical Mover", 48, 46, 66, 40, 72, 72, 68, 70, 74, 42),
        "jacoby brissett": profile("Careful Veteran Bridge", 70, 76, 34, 70, 42, 38, 42, 16, 34, 80),
        "jake browning": profile("Rhythm Backup Operator", 66, 68, 36, 64, 42, 40, 44, 22, 44, 68),
        "jake haener": profile("Undersized Rhythm Reserve", 58, 60, 48, 62, 44, 50, 50, 42, 58, 58),
        "jarrett stidham": profile("Structured Reserve", 62, 66, 36, 62, 48, 38, 44, 18, 48, 66),
        "jj mccarthy": profile("Developmental Movement Passer", 58, 56, 60, 48, 66, 68, 68, 62, 64, 50),
        "joe milton": profile("Raw Cannon", 30, 30, 78, 22, 92, 72, 78, 66, 86, 24),
        "john wolford": profile("Mobile Emergency Rhythm", 52, 56, 56, 58, 36, 64, 52, 58, 54, 60),
        "josh johnson": profile("Mobile Journeyman Reserve", 50, 54, 56, 54, 44, 66, 54, 62, 56, 58),
        "kenny pickett": profile("Cautious Pocket Mover", 58, 60, 54, 58, 42, 58, 54, 48, 60, 56),
        "kyle allen": profile("Traditional Backup Distributor", 62, 66, 30, 66, 38, 30, 36, 12, 46, 68),
        "mac jones": profile("Quick-Game Pocket Backup", 68, 72, 28, 72, 36, 28, 36, 10, 46, 76),
        "mitch trubisky": profile("Mobile Backup Spot Starter", 52, 54, 60, 52, 48, 68, 58, 66, 62, 54),
        "mitchell trubisky": profile("Mobile Backup Spot Starter", 52, 54, 60, 52, 48, 68, 58, 66, 62, 54),
        "nick mullens": profile("Aggressive Reserve Distributor", 58, 60, 34, 54, 60, 30, 48, 12, 62, 50),
        "riley leonard": profile("Young Power Mover", 44, 44, 72, 40, 56, 76, 66, 78, 72, 42),
        "sam ehlinger": profile("Short-Yardage Mobile Reserve", 48, 52, 62, 54, 36, 72, 54, 72, 52, 62),
        "sam hartman": profile("Rhythm Development Backup", 58, 60, 44, 58, 54, 48, 52, 36, 58, 56),
        "sam howell": profile("Aggressive Hold-And-Throw Backup", 42, 38, 70, 30, 78, 62, 76, 56, 86, 30),
        "tanner mckee": profile("Tall Pocket Reserve", 60, 64, 28, 58, 58, 24, 40, 8, 52, 64),
        "taylor heinicke": profile("Gutsy Improviser", 50, 48, 66, 44, 60, 64, 70, 54, 72, 42),
        "teddy bridgewater": profile("Careful Veteran Distributor", 72, 76, 30, 74, 34, 34, 38, 14, 34, 82),
        "tim boyle": profile("Stationary Reserve Arm", 52, 56, 28, 50, 62, 22, 38, 8, 64, 48),
        "trevor siemian": profile("Traditional Emergency QB", 56, 62, 28, 62, 42, 24, 34, 10, 50, 66),
        "trey lance": profile("Raw Power-Athlete Backup", 34, 32, 82, 28, 72, 84, 76, 86, 88, 28),
        "ty simpson": profile("Toolsy Rookie Mover", 42, 40, 70, 36, 76, 72, 76, 70, 78, 36),
        "tyler shough": profile("Late-Blooming Toolsy Starter", 54, 52, 56, 44, 76, 60, 68, 48, 62, 48),
        "tyson bagent": profile("Quick Backup Distributor", 62, 64, 42, 66, 38, 46, 44, 34, 42, 68),
        "will howard": profile("Big-Frame Development Passer", 48, 48, 60, 44, 74, 64, 66, 62, 68, 46),
    }
)


def clamp_rating(value: float) -> float:
    return float(clamp(value, 1.0, 99.0))


class RatingProfileSource:
    def __init__(self, ratings: dict[str, int | float], name: str = "Generated QB") -> None:
        self.name = name
        self.ratings = ratings

    def rating(self, key: str, default: float = 50.0) -> float:
        return float(self.ratings.get(key, default))


def infer_profile(qb: PlayerSnapshot) -> QBBehaviorProfile:
    mental = average(
        [
            qb.rating("processing_speed"),
            qb.rating("play_recognition"),
            qb.rating("composure"),
            qb.rating("consistency"),
        ]
    )
    athletic = average(
        [
            qb.rating("speed"),
            qb.rating("acceleration"),
            qb.rating("agility"),
            qb.rating("elusiveness"),
            qb.rating("carry_vision"),
        ]
    )
    arm = average([qb.rating("throw_power"), qb.rating("pass_accuracy_deep")])
    release = qb.rating("throw_release")
    discipline = qb.rating("discipline")

    pocket_discipline = clamp_rating(mental * 0.58 + discipline * 0.32 + max(0, 72 - athletic) * 0.10)
    rhythm = clamp_rating(mental * 0.62 + release * 0.38)
    pocket_drift = clamp_rating(50 + (athletic - 62) * 0.45 + (arm - 76) * 0.18 - (discipline - 68) * 0.20)
    pressure_escape = clamp_rating(athletic * 0.78 + qb.rating("elusiveness") * 0.22)
    broken_play = clamp_rating(pressure_escape * 0.42 + arm * 0.24 + qb.rating("composure") * 0.18 + pocket_drift * 0.16)
    scramble = clamp_rating(athletic * 0.72 + qb.rating("carry_vision") * 0.20 + pocket_drift * 0.08)
    checkdown = clamp_rating(52 + (release - 68) * 0.35 + (discipline - 68) * 0.32 - (arm - 80) * 0.16)
    deep_aggression = clamp_rating(50 + (arm - 76) * 0.65 + (pocket_drift - 50) * 0.18 - (checkdown - 52) * 0.15)
    sack_risk = clamp_rating(52 + (pocket_drift - 50) * 0.42 - (release - 68) * 0.24 - (discipline - 68) * 0.26)
    throwaway = clamp_rating(50 + (discipline - 68) * 0.45 + (release - 68) * 0.20 - (pocket_drift - 50) * 0.28)

    if athletic >= 78 and deep_aggression >= 66:
        label = "Inferred Creator"
    elif rhythm >= 76 and pocket_discipline >= 72:
        label = "Inferred Rhythm Pocket"
    elif scramble >= 74:
        label = "Inferred Mobile QB"
    elif deep_aggression >= 72:
        label = "Inferred Vertical QB"
    else:
        label = "Inferred Balanced QB"

    return QBBehaviorProfile(
        label=label,
        rhythm=rhythm,
        pocket_discipline=pocket_discipline,
        pocket_drift=pocket_drift,
        checkdown_willingness=checkdown,
        deep_aggression=deep_aggression,
        pressure_escape=pressure_escape,
        broken_play_creation=broken_play,
        scramble_trigger=scramble,
        sack_risk=sack_risk,
        throwaway_discipline=throwaway,
        notes="Inferred from current QB ratings.",
    )


def with_deltas(base: QBBehaviorProfile, *, label: str, notes: str, **deltas: float) -> QBBehaviorProfile:
    values = base.as_dict()
    for field in QB_BEHAVIOR_FIELDS:
        values[field] = clamp(float(values[field]) + float(deltas.get(field, 0.0)), 1, 99)
    values["label"] = label
    values["notes"] = notes
    return profile_from_mapping(values)


def generated_qb_behavior_profile(archetype: str, ratings: dict[str, int | float]) -> QBBehaviorProfile:
    base = infer_profile(RatingProfileSource(ratings))
    archetype = str(archetype or "").strip()
    if archetype == "Pocket passer":
        return with_deltas(
            base,
            label="Generated Pocket Passer",
            notes="Generated from draft QB archetype and true ratings.",
            rhythm=8,
            pocket_discipline=10,
            pocket_drift=-14,
            checkdown_willingness=8,
            deep_aggression=-2,
            pressure_escape=-10,
            broken_play_creation=-6,
            scramble_trigger=-18,
            sack_risk=-5,
            throwaway_discipline=8,
        )
    if archetype == "Rhythm passer":
        return with_deltas(
            base,
            label="Generated Rhythm Passer",
            notes="Generated from draft QB archetype and true ratings.",
            rhythm=12,
            pocket_discipline=6,
            pocket_drift=-10,
            checkdown_willingness=12,
            deep_aggression=-8,
            pressure_escape=-4,
            broken_play_creation=-4,
            scramble_trigger=-10,
            sack_risk=-8,
            throwaway_discipline=10,
        )
    if archetype == "Dual-threat":
        return with_deltas(
            base,
            label="Generated Dual-Threat",
            notes="Generated from draft QB archetype and true ratings.",
            rhythm=-5,
            pocket_discipline=-4,
            pocket_drift=16,
            checkdown_willingness=-5,
            deep_aggression=4,
            pressure_escape=16,
            broken_play_creation=12,
            scramble_trigger=20,
            sack_risk=5,
            throwaway_discipline=-5,
        )
    if archetype == "Toolsy passer":
        return with_deltas(
            base,
            label="Generated Toolsy Passer",
            notes="Generated from draft QB archetype and true ratings.",
            rhythm=-10,
            pocket_discipline=-8,
            pocket_drift=10,
            checkdown_willingness=-12,
            deep_aggression=16,
            pressure_escape=6,
            broken_play_creation=12,
            scramble_trigger=4,
            sack_risk=14,
            throwaway_discipline=-10,
        )
    return with_deltas(
        base,
        label="Generated Balanced QB",
        notes="Generated from draft QB true ratings.",
    )


def metadata_profile(qb) -> QBBehaviorProfile | None:
    metadata = getattr(qb, "metadata", None) or {}
    stored = metadata.get("qb_behavior_profile")
    if isinstance(stored, QBBehaviorProfile):
        return stored
    if isinstance(stored, dict):
        return profile_from_mapping(stored)
    return None


def metadata_source(qb) -> str | None:
    metadata = getattr(qb, "metadata", None) or {}
    source = metadata.get("qb_behavior_source")
    return str(source) if source is not None else None


def qb_behavior_source(qb) -> str:
    stored = metadata_profile(qb)
    stored_source = metadata_source(qb)
    if stored and stored_source != "qb_behavior_named_seed":
        return "stored"
    if normalize_name(qb.name) in QB_STYLE_OVERRIDES:
        return "named"
    if stored:
        return "stored"
    return "inferred"


def qb_behavior_profile(qb: PlayerSnapshot) -> QBBehaviorProfile:
    stored = metadata_profile(qb)
    stored_source = metadata_source(qb)
    if stored and stored_source != "qb_behavior_named_seed":
        return stored
    named = QB_STYLE_OVERRIDES.get(normalize_name(qb.name))
    if named:
        return named
    if stored:
        return stored
    return infer_profile(qb)
