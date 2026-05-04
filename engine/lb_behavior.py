"""Linebacker style profiles for sim and tick-level play behavior.

These are football behavior traits, not hidden personality traits. They let
linebackers with similar ratings play differently as green-dot coverage
players, downhill box defenders, range athletes, blitzers, or rally tacklers.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class LBBehaviorProfile:
    label: str
    trigger_quickness: float
    gap_fit_discipline: float
    scrape_range: float
    traffic_navigation: float
    zone_landmark_depth: float
    man_match_carry: float
    blitz_timing: float
    tackle_finish: float
    rally_support: float
    penalty_control: float
    notes: str = ""

    def as_dict(self) -> dict[str, float | str]:
        return {
            "label": self.label,
            "trigger_quickness": round(self.trigger_quickness, 1),
            "gap_fit_discipline": round(self.gap_fit_discipline, 1),
            "scrape_range": round(self.scrape_range, 1),
            "traffic_navigation": round(self.traffic_navigation, 1),
            "zone_landmark_depth": round(self.zone_landmark_depth, 1),
            "man_match_carry": round(self.man_match_carry, 1),
            "blitz_timing": round(self.blitz_timing, 1),
            "tackle_finish": round(self.tackle_finish, 1),
            "rally_support": round(self.rally_support, 1),
            "penalty_control": round(self.penalty_control, 1),
            "notes": self.notes,
        }


LB_BEHAVIOR_FIELDS = (
    "trigger_quickness",
    "gap_fit_discipline",
    "scrape_range",
    "traffic_navigation",
    "zone_landmark_depth",
    "man_match_carry",
    "blitz_timing",
    "tackle_finish",
    "rally_support",
    "penalty_control",
)


LB_POSITIONS = {"ILB", "LB", "OLB"}


def profile(
    label: str,
    trigger: int,
    gap: int,
    scrape: int,
    traffic: int,
    zone: int,
    man: int,
    blitz: int,
    tackle: int,
    rally: int,
    penalty: int,
    notes: str = "",
) -> LBBehaviorProfile:
    return LBBehaviorProfile(
        label=label,
        trigger_quickness=trigger,
        gap_fit_discipline=gap,
        scrape_range=scrape,
        traffic_navigation=traffic,
        zone_landmark_depth=zone,
        man_match_carry=man,
        blitz_timing=blitz,
        tackle_finish=tackle,
        rally_support=rally,
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


def profile_from_mapping(values: dict[str, Any]) -> LBBehaviorProfile:
    return LBBehaviorProfile(
        label=str(values.get("label") or "Stored LB Profile"),
        trigger_quickness=float(values.get("trigger_quickness", 50)),
        gap_fit_discipline=float(values.get("gap_fit_discipline", 50)),
        scrape_range=float(values.get("scrape_range", 50)),
        traffic_navigation=float(values.get("traffic_navigation", 50)),
        zone_landmark_depth=float(values.get("zone_landmark_depth", 50)),
        man_match_carry=float(values.get("man_match_carry", 50)),
        blitz_timing=float(values.get("blitz_timing", 50)),
        tackle_finish=float(values.get("tackle_finish", 50)),
        rally_support=float(values.get("rally_support", 50)),
        penalty_control=float(values.get("penalty_control", 50)),
        notes=str(values.get("notes") or ""),
    )


def profile_to_db_tuple(player_or_prospect_id: int, season: int | None, profile: LBBehaviorProfile, source: str):
    base = [
        int(player_or_prospect_id),
    ]
    if season is not None:
        base.append(int(season))
    base.extend(
        [
            profile.label,
            int(round(profile.trigger_quickness)),
            int(round(profile.gap_fit_discipline)),
            int(round(profile.scrape_range)),
            int(round(profile.traffic_navigation)),
            int(round(profile.zone_landmark_depth)),
            int(round(profile.man_match_carry)),
            int(round(profile.blitz_timing)),
            int(round(profile.tackle_finish)),
            int(round(profile.rally_support)),
            int(round(profile.penalty_control)),
            source,
            profile.notes,
        ]
    )
    return tuple(base)


def ensure_player_lb_behavior_schema(con) -> None:
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS player_lb_behavior_profiles (
            player_id INTEGER NOT NULL REFERENCES players(player_id) ON DELETE CASCADE,
            season INTEGER NOT NULL,
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
            source TEXT NOT NULL DEFAULT 'manual',
            notes TEXT,
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (player_id, season)
        );

        CREATE INDEX IF NOT EXISTS idx_player_lb_behavior_profiles_season
            ON player_lb_behavior_profiles(season, label);
        """
    )


def player_lb_behavior_table_exists(con) -> bool:
    row = con.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type = 'table'
          AND name = 'player_lb_behavior_profiles'
        """
    ).fetchone()
    return bool(row)


LB_STYLE_OVERRIDES: dict[str, LBBehaviorProfile] = {
    "alex anzalone": profile("Veteran Balanced LB", 74, 74, 74, 72, 72, 70, 68, 70, 72, 78),
    "alex singleton": profile("Volume Tackle LB", 78, 78, 72, 76, 68, 64, 66, 70, 80, 82),
    "anthony hill": profile("Explosive Young Box LB", 74, 70, 86, 78, 68, 76, 78, 78, 76, 62),
    "azeez alshaair": profile("Physical Pursuit LB", 78, 78, 82, 78, 72, 74, 72, 76, 78, 80),
    "blake cashman": profile(
        "Athletic Space LB",
        82,
        82,
        90,
        78,
        80,
        78,
        72,
        80,
        84,
        84,
        "Great athletic profile and range; 4.51 speed shows up in scrape and rally behavior.",
    ),
    "bobby okereke": profile("Length Coverage LB", 82, 82, 84, 80, 82, 80, 70, 80, 84, 84),
    "bobby wagner": profile("Veteran Green-Dot LB", 86, 88, 78, 88, 80, 76, 74, 86, 90, 88),
    "cody barton": profile("Athletic Box LB", 72, 74, 80, 76, 72, 74, 72, 76, 76, 76),
    "daiyan henley": profile("Athletic Coverage LB", 80, 78, 84, 78, 80, 80, 72, 80, 80, 80),
    "demario davis": profile("Veteran Command LB", 86, 88, 82, 86, 82, 76, 78, 82, 86, 90),
    "denzel perryman": profile("Downhill Thumper LB", 74, 78, 72, 78, 68, 68, 70, 74, 72, 76),
    "devin bush": profile("Fast Pursuit LB", 76, 74, 80, 72, 74, 72, 70, 74, 76, 78),
    "devin white": profile("Explosive Pressure LB", 76, 74, 82, 74, 72, 72, 82, 76, 76, 74),
    "dre greenlaw": profile("Fast Physical LB", 80, 80, 84, 80, 74, 74, 74, 80, 80, 84),
    "drue tranquill": profile("Smart Coverage LB", 84, 84, 76, 82, 82, 76, 70, 80, 82, 88),
    "duke riley": profile("Coverage Depth LB", 74, 76, 76, 74, 76, 74, 68, 76, 76, 80),
    "edgerrin cooper": profile("Young Range LB", 74, 72, 80, 72, 70, 72, 72, 74, 76, 74),
    "elandon roberts": profile("Run-Fit Box LB", 74, 78, 72, 78, 68, 66, 72, 72, 72, 78),
    "eric kendricks": profile("Veteran Coverage LB", 78, 78, 72, 78, 76, 72, 68, 70, 76, 80),
    "eric wilson": profile(
        "Veteran Fit LB",
        80,
        82,
        76,
        80,
        78,
        70,
        70,
        78,
        80,
        86,
        "Smart veteran profile with run-fit discipline and dependable rally value.",
    ),
    "ernest jones": profile("Physical Box LB", 76, 78, 78, 80, 72, 70, 70, 78, 78, 80),
    "foyesade oluokun": profile("Volume Range LB", 84, 84, 84, 82, 82, 78, 72, 86, 88, 88),
    "frankie luvu": profile("Pressure Box LB", 78, 78, 78, 78, 70, 70, 82, 76, 76, 80),
    "fred warner": profile("Elite Green-Dot Coverage LB", 94, 92, 90, 90, 94, 90, 74, 90, 92, 96),
    "germaine pratt": profile("Steady Coverage LB", 74, 76, 76, 74, 76, 74, 68, 78, 76, 78),
    "jack campbell": profile("Young Command Box LB", 78, 82, 76, 80, 76, 72, 70, 82, 80, 84),
    "jack sanborn": profile("Instinctive Box LB", 76, 78, 68, 76, 68, 64, 68, 72, 72, 74),
    "jake golday": profile("Developmental Range LB", 70, 70, 86, 72, 70, 76, 74, 74, 76, 66),
    "jeremiah owusukoramoah": profile("Space Matchup LB", 78, 74, 88, 76, 76, 82, 74, 72, 76, 76),
    "jihaad campbell": profile("Young Athletic LB", 74, 72, 82, 74, 72, 76, 76, 74, 74, 72),
    "jordyn brooks": profile("Explosive Tackle LB", 82, 82, 90, 82, 78, 80, 76, 88, 84, 86),
    "josey jewell": profile("Instinctive Box LB", 78, 80, 78, 80, 76, 72, 68, 84, 82, 82),
    "kaden elliss": profile("Pressure Box LB", 76, 76, 76, 76, 70, 70, 80, 74, 74, 80),
    "leo chenal": profile("Downhill Power LB", 72, 78, 74, 82, 66, 66, 76, 76, 74, 74),
    "matt milano": profile("Coverage Instinct LB", 78, 78, 76, 76, 78, 76, 70, 72, 76, 80),
    "nakobe dean": profile("Compact Range LB", 76, 76, 84, 76, 76, 78, 72, 80, 78, 76),
    "nick bolton": profile("Box Command LB", 82, 84, 78, 84, 76, 72, 72, 84, 84, 84),
    "noah sewell": profile("Downhill Box LB", 70, 74, 70, 76, 64, 64, 70, 76, 72, 72),
    "patrick queen": profile("Fast Pursuit LB", 78, 76, 86, 76, 74, 76, 76, 76, 78, 78),
    "payton wilson": profile("Young Range LB", 74, 72, 86, 74, 70, 74, 74, 74, 78, 70),
    "quay walker": profile("Long Range LB", 78, 78, 86, 80, 78, 80, 74, 82, 80, 78),
    "quincy williams": profile("Fast Contact LB", 74, 74, 84, 76, 70, 74, 74, 76, 76, 78),
    "robert spillane": profile("Physical Box LB", 76, 78, 80, 78, 72, 72, 70, 74, 78, 80),
    "ronnie harrison": profile("Converted Safety LB", 76, 74, 86, 74, 78, 82, 72, 76, 76, 78),
    "roquan smith": profile("Elite Pursuit Box LB", 90, 90, 92, 88, 86, 84, 78, 88, 90, 92),
    "shaq thompson": profile("Veteran Space LB", 76, 76, 76, 74, 76, 74, 70, 70, 74, 80),
    "terrel bernard": profile("Fast Coverage LB", 76, 76, 84, 76, 74, 76, 72, 76, 78, 78),
    "tj edwards": profile("Instinctive Box Command LB", 86, 88, 74, 84, 82, 74, 70, 86, 84, 90),
    "tremaine edmunds": profile("Long Zone LB", 78, 80, 78, 80, 78, 74, 70, 78, 80, 82),
    "tyrel dodson": profile("Athletic Hybrid LB", 78, 78, 84, 76, 78, 80, 76, 80, 78, 82),
    "willie gay": profile("Explosive Space LB", 76, 76, 88, 76, 76, 80, 78, 78, 78, 80),
    "zack baun": profile("Versatile Pressure LB", 80, 80, 80, 78, 76, 74, 82, 80, 78, 84),
    "zaire franklin": profile("Volume Box LB", 82, 84, 78, 82, 76, 72, 70, 82, 86, 86),
}


class RatingProfileSource:
    def __init__(self, ratings: dict[str, int | float], name: str = "Generated LB", position: str = "ILB") -> None:
        self.name = name
        self.position = position
        self.ratings = ratings

    def rating(self, key: str, default: float = 50.0) -> float:
        return float(self.ratings.get(key, default))


def infer_profile(linebacker: PlayerSnapshot) -> LBBehaviorProfile:
    position = str(getattr(linebacker, "position", "ILB")).upper()
    athletic = average([linebacker.rating("speed"), linebacker.rating("acceleration"), linebacker.rating("agility")])
    mental = average(
        [
            linebacker.rating("play_recognition"),
            linebacker.rating("processing_speed"),
            linebacker.rating("composure"),
            linebacker.rating("consistency"),
        ]
    )
    trigger = clamp_rating(
        linebacker.rating("run_diagnostics") * 0.28
        + linebacker.rating("play_recognition") * 0.24
        + linebacker.rating("processing_speed") * 0.18
        + linebacker.rating("acceleration") * 0.16
        + linebacker.rating("discipline") * 0.14
    )
    gap = clamp_rating(
        linebacker.rating("gap_integrity") * 0.34
        + linebacker.rating("run_diagnostics") * 0.24
        + linebacker.rating("discipline") * 0.16
        + linebacker.rating("block_shedding") * 0.14
        + linebacker.rating("strength") * 0.12
    )
    scrape = clamp_rating(
        linebacker.rating("pursuit_angle") * 0.28
        + linebacker.rating("speed") * 0.24
        + linebacker.rating("acceleration") * 0.18
        + linebacker.rating("agility") * 0.14
        + linebacker.rating("play_recognition") * 0.10
        + linebacker.rating("stamina") * 0.06
    )
    traffic = clamp_rating(
        linebacker.rating("block_shedding") * 0.28
        + linebacker.rating("gap_integrity") * 0.20
        + linebacker.rating("strength") * 0.16
        + linebacker.rating("balance") * 0.14
        + linebacker.rating("run_diagnostics") * 0.12
        + linebacker.rating("agility") * 0.10
    )
    zone = clamp_rating(
        linebacker.rating("zone_coverage") * 0.34
        + linebacker.rating("coverage_communication") * 0.22
        + linebacker.rating("play_recognition") * 0.16
        + linebacker.rating("processing_speed") * 0.12
        + linebacker.rating("pursuit_angle") * 0.10
        + linebacker.rating("composure") * 0.06
    )
    man = clamp_rating(
        linebacker.rating("man_coverage") * 0.34
        + linebacker.rating("speed") * 0.18
        + linebacker.rating("agility") * 0.16
        + linebacker.rating("acceleration") * 0.12
        + linebacker.rating("processing_speed") * 0.10
        + linebacker.rating("play_recognition") * 0.10
    )
    blitz = clamp_rating(
        linebacker.rating("speed_rush") * 0.22
        + linebacker.rating("power_rush") * 0.18
        + linebacker.rating("acceleration") * 0.18
        + linebacker.rating("pursuit_angle") * 0.14
        + linebacker.rating("rush_plan") * 0.12
        + linebacker.rating("processing_speed") * 0.08
        + linebacker.rating("discipline") * 0.08
    )
    tackle = clamp_rating(
        linebacker.rating("solo_tackle") * 0.26
        + linebacker.rating("tackle_wrap") * 0.24
        + linebacker.rating("open_field_tackle") * 0.16
        + linebacker.rating("hit_power") * 0.12
        + linebacker.rating("balance") * 0.10
        + linebacker.rating("composure") * 0.08
        + linebacker.rating("strength") * 0.04
    )
    rally = clamp_rating(
        linebacker.rating("assist_tackle") * 0.26
        + linebacker.rating("pursuit_angle") * 0.20
        + linebacker.rating("speed") * 0.16
        + linebacker.rating("play_recognition") * 0.14
        + linebacker.rating("coverage_communication") * 0.12
        + linebacker.rating("stamina") * 0.08
        + linebacker.rating("discipline") * 0.04
    )
    penalty = clamp_rating(
        linebacker.rating("discipline") * 0.44
        + linebacker.rating("composure") * 0.20
        + linebacker.rating("consistency") * 0.16
        + linebacker.rating("processing_speed") * 0.10
        + linebacker.rating("play_recognition") * 0.10
    )

    if position == "OLB" and blitz >= average([zone, man]) + 8:
        label = "Inferred Pressure LB"
    elif zone >= 78 and scrape >= 78:
        label = "Inferred Coverage LB"
    elif gap >= 78 and traffic >= 76 and tackle >= 76:
        label = "Inferred Box LB"
    elif scrape >= 82 and athletic >= 80:
        label = "Inferred Range LB"
    elif blitz >= 78:
        label = "Inferred Blitz LB"
    elif mental >= 78:
        label = "Inferred Veteran LB"
    else:
        label = "Inferred Balanced LB"

    return LBBehaviorProfile(
        label=label,
        trigger_quickness=trigger,
        gap_fit_discipline=gap,
        scrape_range=scrape,
        traffic_navigation=traffic,
        zone_landmark_depth=zone,
        man_match_carry=man,
        blitz_timing=blitz,
        tackle_finish=tackle,
        rally_support=rally,
        penalty_control=penalty,
        notes="Inferred from current linebacker ratings.",
    )


def with_deltas(base: LBBehaviorProfile, *, label: str, notes: str, **deltas: float) -> LBBehaviorProfile:
    values = base.as_dict()
    for field in LB_BEHAVIOR_FIELDS:
        values[field] = clamp(float(values[field]) + float(deltas.get(field, 0.0)), 1, 99)
    values["label"] = label
    values["notes"] = notes
    return profile_from_mapping(values)


def generated_lb_behavior_profile(
    archetype: str,
    ratings: dict[str, int | float],
    *,
    position: str = "ILB",
) -> LBBehaviorProfile:
    base = infer_profile(RatingProfileSource(ratings, position=position))
    archetype = str(archetype or "").strip()
    if archetype == "Coverage linebacker":
        return with_deltas(
            base,
            label="Generated Coverage LB",
            notes="Generated from draft LB archetype and true ratings.",
            trigger_quickness=3,
            gap_fit_discipline=0,
            scrape_range=8,
            traffic_navigation=-4,
            zone_landmark_depth=14,
            man_match_carry=10,
            blitz_timing=-5,
            tackle_finish=0,
            rally_support=8,
            penalty_control=5,
        )
    if archetype == "Box linebacker":
        return with_deltas(
            base,
            label="Generated Box LB",
            notes="Generated from draft LB archetype and true ratings.",
            trigger_quickness=8,
            gap_fit_discipline=12,
            scrape_range=0,
            traffic_navigation=12,
            zone_landmark_depth=-6,
            man_match_carry=-8,
            blitz_timing=2,
            tackle_finish=10,
            rally_support=6,
            penalty_control=4,
        )
    if archetype == "Blitzer":
        return with_deltas(
            base,
            label="Generated Blitzer LB",
            notes="Generated from draft LB archetype and true ratings.",
            trigger_quickness=5,
            gap_fit_discipline=-2,
            scrape_range=6,
            traffic_navigation=4,
            zone_landmark_depth=-8,
            man_match_carry=-5,
            blitz_timing=16,
            tackle_finish=5,
            rally_support=1,
            penalty_control=-4,
        )
    if archetype == "Hybrid linebacker":
        return with_deltas(
            base,
            label="Generated Hybrid LB",
            notes="Generated from draft OLB archetype and true ratings.",
            trigger_quickness=5,
            gap_fit_discipline=4,
            scrape_range=10,
            traffic_navigation=2,
            zone_landmark_depth=9,
            man_match_carry=8,
            blitz_timing=6,
            tackle_finish=3,
            rally_support=8,
            penalty_control=4,
        )
    if archetype in {"Speed rusher", "Power edge"}:
        return with_deltas(
            base,
            label="Generated Pressure OLB",
            notes="Generated from draft OLB pass-rush archetype and true ratings.",
            trigger_quickness=2,
            gap_fit_discipline=0,
            scrape_range=4,
            traffic_navigation=2,
            zone_landmark_depth=-8,
            man_match_carry=-6,
            blitz_timing=14,
            tackle_finish=4,
            rally_support=0,
            penalty_control=-2,
        )
    if archetype == "Run-setting edge":
        return with_deltas(
            base,
            label="Generated Run-Fit OLB",
            notes="Generated from draft OLB run-setting archetype and true ratings.",
            trigger_quickness=4,
            gap_fit_discipline=12,
            scrape_range=2,
            traffic_navigation=10,
            zone_landmark_depth=-4,
            man_match_carry=-5,
            blitz_timing=4,
            tackle_finish=8,
            rally_support=5,
            penalty_control=6,
        )
    return with_deltas(
        base,
        label="Generated Balanced LB",
        notes="Generated from draft LB true ratings.",
    )


def metadata_profile(linebacker) -> LBBehaviorProfile | None:
    metadata = getattr(linebacker, "metadata", None) or {}
    stored = metadata.get("lb_behavior_profile")
    if isinstance(stored, LBBehaviorProfile):
        return stored
    if isinstance(stored, dict):
        return profile_from_mapping(stored)
    return None


def metadata_source(linebacker) -> str | None:
    metadata = getattr(linebacker, "metadata", None) or {}
    source = metadata.get("lb_behavior_source")
    return str(source) if source is not None else None


def lb_behavior_source(linebacker) -> str:
    stored = metadata_profile(linebacker)
    stored_source = metadata_source(linebacker)
    if stored and stored_source != "lb_behavior_named_seed":
        return "stored"
    if normalize_name(linebacker.name) in LB_STYLE_OVERRIDES:
        return "named"
    if stored:
        return "stored"
    return "inferred"


def lb_behavior_profile(linebacker: PlayerSnapshot) -> LBBehaviorProfile:
    stored = metadata_profile(linebacker)
    stored_source = metadata_source(linebacker)
    if stored and stored_source != "lb_behavior_named_seed":
        return stored
    named = LB_STYLE_OVERRIDES.get(normalize_name(linebacker.name))
    if named:
        return named
    if stored:
        return stored
    return infer_profile(linebacker)
