"""Secondary defender style profiles for sim and tick-level play behavior.

These are football behavior traits, not hidden personality traits. They let
corners, nickels, and safeties with similar ratings play differently as press
corners, mirror corners, slot traffic players, deep safeties, ballhawks, box
safeties, and versatile match defenders.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SecondaryBehaviorProfile:
    label: str
    press_timing: float
    man_mirror: float
    zone_eye_discipline: float
    break_trigger: float
    deep_range: float
    ball_play_timing: float
    catch_point_compete: float
    slot_traffic: float
    run_support_fit: float
    tackle_finish: float
    penalty_control: float
    notes: str = ""

    def as_dict(self) -> dict[str, float | str]:
        return {
            "label": self.label,
            "press_timing": round(self.press_timing, 1),
            "man_mirror": round(self.man_mirror, 1),
            "zone_eye_discipline": round(self.zone_eye_discipline, 1),
            "break_trigger": round(self.break_trigger, 1),
            "deep_range": round(self.deep_range, 1),
            "ball_play_timing": round(self.ball_play_timing, 1),
            "catch_point_compete": round(self.catch_point_compete, 1),
            "slot_traffic": round(self.slot_traffic, 1),
            "run_support_fit": round(self.run_support_fit, 1),
            "tackle_finish": round(self.tackle_finish, 1),
            "penalty_control": round(self.penalty_control, 1),
            "notes": self.notes,
        }


SECONDARY_BEHAVIOR_FIELDS = (
    "press_timing",
    "man_mirror",
    "zone_eye_discipline",
    "break_trigger",
    "deep_range",
    "ball_play_timing",
    "catch_point_compete",
    "slot_traffic",
    "run_support_fit",
    "tackle_finish",
    "penalty_control",
)


SECONDARY_POSITIONS = {"CB", "NB", "FS", "SS", "S"}


def profile(
    label: str,
    press: int,
    man: int,
    zone: int,
    trigger: int,
    range_: int,
    ball: int,
    catch_point: int,
    slot: int,
    run: int,
    tackle: int,
    penalty: int,
    notes: str = "",
) -> SecondaryBehaviorProfile:
    return SecondaryBehaviorProfile(
        label=label,
        press_timing=press,
        man_mirror=man,
        zone_eye_discipline=zone,
        break_trigger=trigger,
        deep_range=range_,
        ball_play_timing=ball,
        catch_point_compete=catch_point,
        slot_traffic=slot,
        run_support_fit=run,
        tackle_finish=tackle,
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


def profile_from_mapping(values: dict[str, Any]) -> SecondaryBehaviorProfile:
    return SecondaryBehaviorProfile(
        label=str(values.get("label") or "Stored Secondary Profile"),
        press_timing=float(values.get("press_timing", 50)),
        man_mirror=float(values.get("man_mirror", 50)),
        zone_eye_discipline=float(values.get("zone_eye_discipline", 50)),
        break_trigger=float(values.get("break_trigger", 50)),
        deep_range=float(values.get("deep_range", 50)),
        ball_play_timing=float(values.get("ball_play_timing", 50)),
        catch_point_compete=float(values.get("catch_point_compete", 50)),
        slot_traffic=float(values.get("slot_traffic", 50)),
        run_support_fit=float(values.get("run_support_fit", 50)),
        tackle_finish=float(values.get("tackle_finish", 50)),
        penalty_control=float(values.get("penalty_control", 50)),
        notes=str(values.get("notes") or ""),
    )


def profile_to_db_tuple(
    player_or_prospect_id: int,
    season: int | None,
    profile: SecondaryBehaviorProfile,
    source: str,
):
    base = [
        int(player_or_prospect_id),
    ]
    if season is not None:
        base.append(int(season))
    base.extend(
        [
            profile.label,
            int(round(profile.press_timing)),
            int(round(profile.man_mirror)),
            int(round(profile.zone_eye_discipline)),
            int(round(profile.break_trigger)),
            int(round(profile.deep_range)),
            int(round(profile.ball_play_timing)),
            int(round(profile.catch_point_compete)),
            int(round(profile.slot_traffic)),
            int(round(profile.run_support_fit)),
            int(round(profile.tackle_finish)),
            int(round(profile.penalty_control)),
            source,
            profile.notes,
        ]
    )
    return tuple(base)


def ensure_player_secondary_behavior_schema(con) -> None:
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS player_secondary_behavior_profiles (
            player_id INTEGER NOT NULL REFERENCES players(player_id) ON DELETE CASCADE,
            season INTEGER NOT NULL,
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
            source TEXT NOT NULL DEFAULT 'manual',
            notes TEXT,
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (player_id, season)
        );

        CREATE INDEX IF NOT EXISTS idx_player_secondary_behavior_profiles_season
            ON player_secondary_behavior_profiles(season, label);
        """
    )


def player_secondary_behavior_table_exists(con) -> bool:
    row = con.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type = 'table'
          AND name = 'player_secondary_behavior_profiles'
        """
    ).fetchone()
    return bool(row)


SECONDARY_STYLE_OVERRIDES: dict[str, SecondaryBehaviorProfile] = {
    "aj terrell": profile("Long Match Corner", 82, 84, 82, 82, 82, 80, 82, 76, 76, 76, 84),
    "alontae taylor": profile("Physical Slot Corner", 82, 78, 76, 78, 76, 76, 80, 84, 82, 80, 74),
    "alohi gilman": profile("Reliable Deep Safety", 70, 72, 80, 80, 82, 80, 74, 74, 78, 78, 84),
    "amani hooker": profile("Smart Box Safety", 70, 72, 78, 78, 76, 76, 76, 78, 84, 82, 84),
    "amik robertson": profile("Competitive Slot Corner", 76, 78, 76, 78, 72, 76, 76, 84, 80, 78, 76),
    "andre cisco": profile("Range Ball Safety", 68, 72, 80, 82, 84, 84, 76, 72, 74, 74, 78),
    "antoine winfield": profile("Elite Versatile Safety", 76, 82, 88, 90, 88, 90, 84, 86, 92, 88, 90),
    "asante samuel": profile("Instinctive Off Corner", 72, 80, 82, 84, 80, 84, 78, 76, 72, 72, 76),
    "billy bowman": profile("Young Ball Nickel", 72, 76, 78, 80, 78, 84, 78, 82, 78, 76, 70),
    "brandon jones": profile("Explosive Box Safety", 70, 74, 76, 80, 80, 78, 78, 80, 86, 82, 76),
    "brian branch": profile("Elite Slot Safety", 80, 86, 88, 90, 82, 88, 84, 94, 88, 86, 88),
    "bryce hall": profile("Long Zone Corner", 76, 76, 80, 78, 78, 76, 78, 74, 74, 74, 82),
    "budda baker": profile("Explosive Rally Safety", 70, 78, 82, 86, 82, 84, 80, 86, 92, 88, 84),
    "byron murphy": profile(
        "Versatile Nickel Corner",
        80,
        82,
        82,
        84,
        78,
        82,
        80,
        86,
        80,
        80,
        84,
        "Vikings corner profile: comfortable inside/outside, smart trigger, reliable support.",
    ),
    "caleb downs": profile("Rare Young Safety", 74, 82, 86, 88, 88, 86, 82, 86, 88, 86, 84),
    "cam bynum": profile("Zone Communication Safety", 68, 74, 84, 84, 84, 82, 76, 78, 78, 78, 88),
    "cam lewis": profile("Depth Slot Safety", 70, 70, 72, 72, 70, 70, 72, 76, 78, 76, 74),
    "carlton davis": profile("Press Boundary Corner", 86, 82, 76, 78, 76, 76, 84, 72, 76, 78, 78),
    "charvarius ward": profile("Sticky Press Corner", 86, 84, 78, 82, 82, 80, 84, 74, 76, 78, 78),
    "chris johnson": profile("Toolsy Speed Corner", 76, 78, 74, 76, 82, 74, 76, 72, 72, 72, 70),
    "christian benford": profile("Zone Match Corner", 78, 82, 84, 84, 82, 82, 80, 76, 78, 78, 84),
    "christian gonzalez": profile("Smooth Mirror Corner", 82, 88, 84, 84, 88, 82, 84, 76, 76, 76, 82),
    "coby bryant": profile("Converted Safety Nickel", 76, 78, 80, 82, 78, 80, 80, 86, 82, 80, 82),
    "cooper dejean": profile("Instinctive Nickel Playmaker", 78, 82, 86, 88, 84, 86, 84, 90, 88, 84, 84),
    "cj gardnerjohnson": profile("Aggressive Ball Safety", 76, 80, 82, 84, 82, 88, 82, 86, 84, 80, 72),
    "daron bland": profile("Ballhawk Corner", 78, 82, 80, 86, 82, 92, 84, 76, 76, 76, 76),
    "dax hill": profile("Athletic Match DB", 78, 82, 78, 82, 84, 78, 78, 84, 78, 76, 76),
    "denzel ward": profile("Explosive Mirror Corner", 82, 88, 80, 86, 86, 82, 82, 74, 74, 74, 78),
    "derek stingley": profile("Athletic Man Ballhawk", 84, 88, 82, 86, 86, 88, 86, 76, 74, 76, 82),
    "derwin james": profile("Star Box Match Safety", 78, 84, 84, 86, 84, 84, 88, 90, 92, 88, 82),
    "devon witherspoon": profile("Aggressive Match Nickel", 84, 86, 82, 88, 80, 84, 84, 88, 86, 84, 74),
    "dj reed": profile("Competitive Mirror Corner", 78, 82, 80, 82, 78, 80, 78, 78, 78, 78, 84),
    "dillon thieneman": profile("Young Deep Safety", 68, 72, 78, 80, 84, 80, 74, 74, 74, 74, 72),
    "elijah hicks": profile("Rally Safety", 68, 70, 74, 76, 74, 74, 72, 76, 82, 80, 76),
    "eric stokes": profile("Speed Boundary Corner", 76, 80, 76, 78, 84, 74, 76, 72, 72, 72, 74),
    "isaiah rodgers": profile(
        "Speed Match Corner",
        76,
        80,
        76,
        80,
        84,
        78,
        76,
        76,
        74,
        74,
        76,
        "Vikings corner profile: speed and recovery show up more than catch-point size.",
    ),
    "jaquan brisker": profile("Physical Box Safety", 70, 74, 76, 78, 76, 76, 78, 80, 86, 84, 76),
    "jalen pitre": profile("Aggressive Slot Safety", 74, 80, 78, 82, 78, 82, 80, 88, 86, 80, 70),
    "jalen ramsey": profile("Veteran Match Safety", 82, 86, 84, 86, 84, 86, 88, 82, 84, 82, 84),
    "jalen thompson": profile("Steady Split Safety", 68, 72, 78, 78, 80, 78, 76, 76, 80, 78, 82),
    "james pierre": profile("Depth Boundary Corner", 72, 74, 72, 72, 76, 70, 74, 70, 72, 72, 72),
    "jarrian jones": profile("Young Slot Corner", 74, 76, 76, 78, 74, 78, 76, 82, 76, 76, 72),
    "jay ward": profile("Developmental Deep Safety", 66, 70, 76, 78, 82, 76, 72, 74, 76, 74, 72),
    "jaycee horn": profile("Physical Press Corner", 88, 84, 78, 82, 80, 80, 86, 74, 78, 78, 76),
    "jaylon johnson": profile("Physical Press Corner", 86, 86, 82, 84, 82, 82, 86, 76, 78, 80, 84),
    "jevn holland": profile("Range Match Safety", 72, 80, 84, 86, 88, 84, 80, 84, 82, 80, 84),
    "jevon holland": profile("Range Match Safety", 72, 80, 84, 86, 88, 84, 80, 84, 82, 80, 84),
    "jeremy chinn": profile("Big Box Safety", 70, 74, 76, 78, 80, 76, 78, 82, 88, 84, 76),
    "jessie bates": profile("Elite Split Safety", 70, 78, 90, 90, 90, 92, 82, 80, 82, 80, 90),
    "jordan whitehead": profile("Physical Box Safety", 68, 72, 74, 76, 74, 76, 76, 78, 86, 84, 76),
    "jourdan lewis": profile("Veteran Slot Corner", 78, 80, 78, 82, 74, 80, 78, 88, 80, 78, 84),
    "joshua metellus": profile(
        "Versatile Big Nickel Safety",
        74,
        78,
        80,
        82,
        78,
        80,
        80,
        88,
        88,
        84,
        82,
        "Vikings profile: safety/nickel usage, reliable support, and strong slot-traffic value.",
    ),
    "juju brents": profile("Long Press Corner", 84, 78, 76, 76, 78, 76, 84, 72, 76, 76, 72),
    "justin reid": profile("Versatile Split Safety", 70, 76, 82, 82, 82, 80, 78, 80, 82, 80, 84),
    "jyon holland": profile("Range Match Safety", 72, 80, 84, 86, 88, 84, 80, 84, 82, 80, 84),
    "kader kohou": profile("Competitive Slot Corner", 76, 80, 78, 80, 74, 78, 78, 86, 78, 76, 78),
    "kam curl": profile("Smart Box Safety", 68, 74, 80, 80, 78, 80, 78, 82, 86, 84, 86),
    "kamren kinchens": profile("Young Ball Safety", 66, 72, 78, 80, 82, 84, 76, 74, 74, 74, 70),
    "kenny moore": profile("Elite Slot Corner", 78, 84, 84, 88, 78, 86, 82, 94, 84, 82, 86),
    "kerby joseph": profile("Deep Ballhawk Safety", 66, 74, 84, 86, 88, 92, 82, 76, 78, 76, 78),
    "kevin byard": profile("Veteran Deep Safety", 68, 76, 86, 86, 86, 86, 78, 76, 80, 78, 88),
    "kyler gordon": profile("Physical Slot Corner", 80, 82, 80, 84, 78, 82, 82, 90, 84, 82, 78),
    "kyle dugger": profile("Power Box Safety", 70, 74, 76, 78, 78, 76, 80, 82, 88, 86, 76),
    "kyle hamilton": profile("Elite Big Slot Safety", 78, 86, 90, 90, 88, 90, 88, 94, 92, 88, 88),
    "ljarius sneed": profile("Press Match Corner", 88, 84, 78, 82, 80, 80, 84, 76, 80, 80, 74),
    "malaki starks": profile("Young Range Safety", 70, 78, 82, 84, 86, 82, 78, 82, 82, 80, 76),
    "malik hooker": profile("Deep Centerfielder", 64, 70, 80, 82, 86, 84, 76, 70, 74, 72, 80),
    "marcus jones": profile("Quick Slot Return DB", 72, 78, 76, 80, 78, 80, 72, 86, 74, 72, 76),
    "marlon humphrey": profile("Power Slot Match Corner", 86, 84, 80, 84, 80, 82, 86, 88, 84, 84, 76),
    "marshon lattimore": profile("Veteran Press Corner", 84, 84, 78, 80, 82, 78, 84, 74, 76, 76, 76),
    "mike sainristil": profile("Smart Slot Corner", 76, 82, 82, 84, 76, 82, 78, 90, 82, 80, 86),
    "minkah fitzpatrick": profile("Versatile Ballhawk DB", 76, 84, 88, 90, 90, 92, 84, 88, 86, 84, 90),
    "patrick surtain": profile("Elite Match Eraser", 88, 94, 88, 90, 88, 86, 88, 76, 76, 82, 92),
    "pat surtain": profile("Elite Match Eraser", 88, 94, 88, 90, 88, 86, 88, 76, 76, 82, 92),
    "quan martin": profile("Range Split Safety", 68, 74, 80, 80, 82, 78, 76, 78, 80, 78, 78),
    "quentin lake": profile("Smart Hybrid Safety", 70, 76, 80, 82, 80, 82, 78, 84, 82, 80, 84),
    "quinyon mitchell": profile("Young Press Mirror CB", 84, 86, 82, 84, 84, 82, 84, 76, 76, 78, 82),
    "reed blankenship": profile("Steady Deep Safety", 66, 72, 80, 80, 80, 78, 76, 74, 80, 78, 82),
    "riq woolen": profile("Length Speed Corner", 82, 82, 76, 80, 90, 82, 86, 72, 74, 74, 70),
    "sauce gardner": profile("Long Press Island", 92, 90, 86, 88, 86, 82, 90, 74, 74, 78, 86),
    "talanoa hufanga": profile("Instinctive Box Safety", 70, 78, 82, 86, 80, 84, 82, 86, 90, 86, 78),
    "taron johnson": profile("Elite Nickel Corner", 80, 84, 84, 88, 78, 86, 82, 94, 86, 84, 86),
    "tavierre thomas": profile("Slot Safety Support", 72, 74, 74, 76, 72, 74, 76, 84, 82, 80, 76),
    "terell smith": profile("Depth Press Corner", 76, 74, 70, 72, 76, 70, 76, 70, 72, 72, 70),
    "terrell smith": profile("Depth Press Corner", 76, 74, 70, 72, 76, 70, 76, 70, 72, 72, 70),
    "theo jackson": profile("Depth Split Safety", 68, 72, 74, 76, 76, 74, 72, 74, 76, 76, 78),
    "travis hunter": profile("Rare Two-Way Ball CB", 82, 86, 84, 86, 88, 90, 88, 78, 76, 76, 78),
    "trent mcduffie": profile("Smart Match Corner", 82, 88, 86, 88, 82, 84, 82, 86, 82, 80, 90),
    "trevon diggs": profile("Risk-Reward Ballhawk CB", 76, 80, 76, 86, 82, 94, 84, 72, 70, 70, 68),
    "trevon moehrig": profile("Versatile Safety", 70, 76, 82, 82, 82, 82, 78, 82, 82, 80, 82),
    "tyrique stevenson": profile("Physical Press Corner", 82, 78, 74, 76, 78, 76, 82, 74, 78, 78, 70),
    "xavier mckinney": profile("Split Safety Ballhawk", 70, 78, 86, 88, 88, 90, 82, 80, 82, 80, 86),
}


class RatingProfileSource:
    def __init__(self, ratings: dict[str, int | float], name: str = "Generated DB", position: str = "CB") -> None:
        self.name = name
        self.position = position
        self.ratings = ratings

    def rating(self, key: str, default: float = 50.0) -> float:
        return float(self.ratings.get(key, default))


def infer_profile(defender: PlayerSnapshot) -> SecondaryBehaviorProfile:
    position = str(getattr(defender, "position", "CB")).upper()
    athletic = average([defender.rating("speed"), defender.rating("acceleration"), defender.rating("agility")])
    mental = average(
        [
            defender.rating("play_recognition"),
            defender.rating("processing_speed"),
            defender.rating("composure"),
            defender.rating("consistency"),
        ]
    )
    press = clamp_rating(
        defender.rating("press_coverage") * 0.34
        + defender.rating("man_coverage") * 0.16
        + defender.rating("strength") * 0.12
        + defender.rating("agility") * 0.10
        + defender.rating("processing_speed") * 0.10
        + defender.rating("discipline") * 0.10
        + defender.rating("play_recognition") * 0.08
    )
    man = clamp_rating(
        defender.rating("man_coverage") * 0.32
        + defender.rating("agility") * 0.18
        + defender.rating("speed") * 0.14
        + defender.rating("acceleration") * 0.12
        + defender.rating("processing_speed") * 0.10
        + defender.rating("press_coverage") * 0.07
        + defender.rating("play_recognition") * 0.07
    )
    zone = clamp_rating(
        defender.rating("zone_coverage") * 0.32
        + defender.rating("coverage_communication") * 0.20
        + defender.rating("play_recognition") * 0.18
        + defender.rating("processing_speed") * 0.12
        + defender.rating("zone_recovery") * 0.10
        + defender.rating("discipline") * 0.08
    )
    trigger = clamp_rating(
        defender.rating("zone_recovery") * 0.20
        + defender.rating("play_recognition") * 0.20
        + defender.rating("processing_speed") * 0.16
        + defender.rating("acceleration") * 0.14
        + defender.rating("agility") * 0.12
        + defender.rating("ball_skills") * 0.10
        + defender.rating("discipline") * 0.08
    )
    range_ = clamp_rating(
        defender.rating("speed") * 0.24
        + defender.rating("acceleration") * 0.18
        + defender.rating("zone_recovery") * 0.18
        + defender.rating("zone_coverage") * 0.14
        + defender.rating("play_recognition") * 0.12
        + defender.rating("agility") * 0.08
        + defender.rating("stamina") * 0.06
    )
    ball = clamp_rating(
        defender.rating("ball_skills") * 0.30
        + defender.rating("play_recognition") * 0.18
        + defender.rating("zone_recovery") * 0.16
        + defender.rating("hands") * 0.12
        + defender.rating("processing_speed") * 0.10
        + defender.rating("composure") * 0.08
        + defender.rating("agility") * 0.06
    )
    catch_point = clamp_rating(
        defender.rating("ball_skills") * 0.22
        + defender.rating("contested_catch") * 0.18
        + defender.rating("press_coverage") * 0.15
        + defender.rating("strength") * 0.12
        + defender.rating("balance") * 0.10
        + defender.rating("hands") * 0.10
        + defender.rating("composure") * 0.07
        + defender.rating("man_coverage") * 0.06
    )
    slot = clamp_rating(
        defender.rating("traffic_navigation") * 0.18
        + defender.rating("agility") * 0.16
        + defender.rating("man_coverage") * 0.14
        + defender.rating("zone_coverage") * 0.14
        + defender.rating("processing_speed") * 0.12
        + defender.rating("play_recognition") * 0.10
        + defender.rating("open_field_tackle") * 0.08
        + defender.rating("press_coverage") * 0.08
    )
    run = clamp_rating(
        defender.rating("run_diagnostics") * 0.18
        + defender.rating("pursuit_angle") * 0.16
        + defender.rating("open_field_tackle") * 0.16
        + defender.rating("assist_tackle") * 0.12
        + defender.rating("solo_tackle") * 0.12
        + defender.rating("gap_integrity") * 0.10
        + defender.rating("play_recognition") * 0.10
        + defender.rating("strength") * 0.06
    )
    tackle = clamp_rating(
        defender.rating("open_field_tackle") * 0.24
        + defender.rating("solo_tackle") * 0.22
        + defender.rating("tackle_wrap") * 0.20
        + defender.rating("pursuit_angle") * 0.10
        + defender.rating("hit_power") * 0.10
        + defender.rating("balance") * 0.08
        + defender.rating("composure") * 0.06
    )
    penalty = clamp_rating(
        defender.rating("discipline") * 0.44
        + defender.rating("composure") * 0.20
        + defender.rating("consistency") * 0.16
        + defender.rating("processing_speed") * 0.10
        + defender.rating("play_recognition") * 0.10
    )

    if position == "NB" or slot >= average([man, zone]) + 6:
        label = "Inferred Slot DB"
    elif position in {"FS", "S"} and range_ >= 78 and zone >= 76:
        label = "Inferred Deep Safety"
    elif position in {"SS", "S"} and run >= 78 and tackle >= 76:
        label = "Inferred Box Safety"
    elif ball >= 82 and trigger >= 78:
        label = "Inferred Ballhawk DB"
    elif press >= 82 and man >= 78:
        label = "Inferred Press Corner"
    elif man >= 82:
        label = "Inferred Mirror Corner"
    elif zone >= 82:
        label = "Inferred Zone DB"
    elif athletic >= 82 and range_ >= 80:
        label = "Inferred Range DB"
    elif mental >= 78:
        label = "Inferred Veteran DB"
    else:
        label = "Inferred Balanced DB"

    return SecondaryBehaviorProfile(
        label=label,
        press_timing=press,
        man_mirror=man,
        zone_eye_discipline=zone,
        break_trigger=trigger,
        deep_range=range_,
        ball_play_timing=ball,
        catch_point_compete=catch_point,
        slot_traffic=slot,
        run_support_fit=run,
        tackle_finish=tackle,
        penalty_control=penalty,
        notes="Inferred from current secondary ratings.",
    )


def with_deltas(
    base: SecondaryBehaviorProfile,
    *,
    label: str,
    notes: str,
    **deltas: float,
) -> SecondaryBehaviorProfile:
    values = base.as_dict()
    for field in SECONDARY_BEHAVIOR_FIELDS:
        values[field] = clamp(float(values[field]) + float(deltas.get(field, 0.0)), 1, 99)
    values["label"] = label
    values["notes"] = notes
    return profile_from_mapping(values)


def generated_secondary_behavior_profile(
    archetype: str,
    ratings: dict[str, int | float],
    *,
    position: str = "CB",
) -> SecondaryBehaviorProfile:
    base = infer_profile(RatingProfileSource(ratings, position=position))
    archetype = str(archetype or "").strip()
    if archetype == "Man corner":
        return with_deltas(
            base,
            label="Generated Man Corner",
            notes="Generated from draft secondary archetype and true ratings.",
            press_timing=5,
            man_mirror=8,
            zone_eye_discipline=-3,
            break_trigger=2,
            deep_range=2,
            ball_play_timing=1,
            catch_point_compete=4,
            slot_traffic=0,
            run_support_fit=-2,
            tackle_finish=-1,
            penalty_control=-1,
        )
    if archetype == "Zone corner":
        return with_deltas(
            base,
            label="Generated Zone Corner",
            notes="Generated from draft secondary archetype and true ratings.",
            press_timing=-2,
            man_mirror=-1,
            zone_eye_discipline=8,
            break_trigger=6,
            deep_range=4,
            ball_play_timing=5,
            catch_point_compete=0,
            slot_traffic=3,
            run_support_fit=2,
            tackle_finish=1,
            penalty_control=4,
        )
    if archetype == "Slot corner":
        return with_deltas(
            base,
            label="Generated Slot Corner",
            notes="Generated from draft secondary archetype and true ratings.",
            press_timing=3,
            man_mirror=5,
            zone_eye_discipline=5,
            break_trigger=6,
            deep_range=-4,
            ball_play_timing=3,
            catch_point_compete=0,
            slot_traffic=9,
            run_support_fit=5,
            tackle_finish=3,
            penalty_control=3,
        )
    if archetype == "Deep safety":
        return with_deltas(
            base,
            label="Generated Deep Safety",
            notes="Generated from draft safety archetype and true ratings.",
            press_timing=-6,
            man_mirror=0,
            zone_eye_discipline=7,
            break_trigger=6,
            deep_range=10,
            ball_play_timing=6,
            catch_point_compete=2,
            slot_traffic=-2,
            run_support_fit=-2,
            tackle_finish=0,
            penalty_control=4,
        )
    if archetype == "Box safety":
        return with_deltas(
            base,
            label="Generated Box Safety",
            notes="Generated from draft safety archetype and true ratings.",
            press_timing=-2,
            man_mirror=2,
            zone_eye_discipline=2,
            break_trigger=4,
            deep_range=-4,
            ball_play_timing=2,
            catch_point_compete=4,
            slot_traffic=5,
            run_support_fit=10,
            tackle_finish=8,
            penalty_control=2,
        )
    if archetype == "Versatile safety":
        return with_deltas(
            base,
            label="Generated Versatile Safety",
            notes="Generated from draft safety archetype and true ratings.",
            press_timing=0,
            man_mirror=4,
            zone_eye_discipline=5,
            break_trigger=6,
            deep_range=5,
            ball_play_timing=4,
            catch_point_compete=3,
            slot_traffic=6,
            run_support_fit=6,
            tackle_finish=5,
            penalty_control=4,
        )
    return with_deltas(
        base,
        label="Generated Balanced DB",
        notes="Generated from draft secondary true ratings.",
    )


def metadata_profile(defender) -> SecondaryBehaviorProfile | None:
    metadata = getattr(defender, "metadata", None) or {}
    stored = metadata.get("secondary_behavior_profile")
    if isinstance(stored, SecondaryBehaviorProfile):
        return stored
    if isinstance(stored, dict):
        return profile_from_mapping(stored)
    return None


def metadata_source(defender) -> str | None:
    metadata = getattr(defender, "metadata", None) or {}
    source = metadata.get("secondary_behavior_source")
    return str(source) if source is not None else None


def secondary_behavior_source(defender) -> str:
    stored = metadata_profile(defender)
    stored_source = metadata_source(defender)
    if stored and stored_source != "secondary_behavior_named_seed":
        return "stored"
    if normalize_name(defender.name) in SECONDARY_STYLE_OVERRIDES:
        return "named"
    if stored:
        return "stored"
    return "inferred"


def secondary_behavior_profile(defender: PlayerSnapshot) -> SecondaryBehaviorProfile:
    stored = metadata_profile(defender)
    stored_source = metadata_source(defender)
    if stored and stored_source != "secondary_behavior_named_seed":
        return stored
    named = SECONDARY_STYLE_OVERRIDES.get(normalize_name(defender.name))
    if named:
        return named
    if stored:
        return stored
    return infer_profile(defender)
