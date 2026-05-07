"""Running back style profiles for sim and tick-level play behavior.

These are football behavior traits, not hidden personality traits. They give
the resolver a way to make backs with similar ratings play differently.
All values are 0-100, where 50 is neutral.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RBBehaviorProfile:
    label: str
    early_down_gravity: float
    patience: float
    one_cut_decisiveness: float
    bounce_tendency: float
    home_run_hunting: float
    contact_appetite: float
    space_creation: float
    pass_game_usage: float
    short_yardage_trust: float
    ball_security_mindset: float
    notes: str = ""

    def as_dict(self) -> dict[str, float | str]:
        return {
            "label": self.label,
            "early_down_gravity": round(self.early_down_gravity, 1),
            "patience": round(self.patience, 1),
            "one_cut_decisiveness": round(self.one_cut_decisiveness, 1),
            "bounce_tendency": round(self.bounce_tendency, 1),
            "home_run_hunting": round(self.home_run_hunting, 1),
            "contact_appetite": round(self.contact_appetite, 1),
            "space_creation": round(self.space_creation, 1),
            "pass_game_usage": round(self.pass_game_usage, 1),
            "short_yardage_trust": round(self.short_yardage_trust, 1),
            "ball_security_mindset": round(self.ball_security_mindset, 1),
            "notes": self.notes,
        }


RB_BEHAVIOR_FIELDS = (
    "early_down_gravity",
    "patience",
    "one_cut_decisiveness",
    "bounce_tendency",
    "home_run_hunting",
    "contact_appetite",
    "space_creation",
    "pass_game_usage",
    "short_yardage_trust",
    "ball_security_mindset",
)


def profile(
    label: str,
    early_down: int,
    patience: int,
    one_cut: int,
    bounce: int,
    home_run: int,
    contact: int,
    space: int,
    pass_usage: int,
    short_yardage: int,
    security: int,
    notes: str = "",
) -> RBBehaviorProfile:
    return RBBehaviorProfile(
        label=label,
        early_down_gravity=early_down,
        patience=patience,
        one_cut_decisiveness=one_cut,
        bounce_tendency=bounce,
        home_run_hunting=home_run,
        contact_appetite=contact,
        space_creation=space,
        pass_game_usage=pass_usage,
        short_yardage_trust=short_yardage,
        ball_security_mindset=security,
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


def profile_from_mapping(values: dict[str, Any]) -> RBBehaviorProfile:
    return RBBehaviorProfile(
        label=str(values.get("label") or "Stored RB Profile"),
        early_down_gravity=float(values.get("early_down_gravity", 50)),
        patience=float(values.get("patience", 50)),
        one_cut_decisiveness=float(values.get("one_cut_decisiveness", 50)),
        bounce_tendency=float(values.get("bounce_tendency", 50)),
        home_run_hunting=float(values.get("home_run_hunting", 50)),
        contact_appetite=float(values.get("contact_appetite", 50)),
        space_creation=float(values.get("space_creation", 50)),
        pass_game_usage=float(values.get("pass_game_usage", 50)),
        short_yardage_trust=float(values.get("short_yardage_trust", 50)),
        ball_security_mindset=float(values.get("ball_security_mindset", 50)),
        notes=str(values.get("notes") or ""),
    )


def profile_to_db_tuple(player_or_prospect_id: int, season: int | None, profile: RBBehaviorProfile, source: str):
    base = [
        int(player_or_prospect_id),
    ]
    if season is not None:
        base.append(int(season))
    base.extend(
        [
            profile.label,
            int(round(profile.early_down_gravity)),
            int(round(profile.patience)),
            int(round(profile.one_cut_decisiveness)),
            int(round(profile.bounce_tendency)),
            int(round(profile.home_run_hunting)),
            int(round(profile.contact_appetite)),
            int(round(profile.space_creation)),
            int(round(profile.pass_game_usage)),
            int(round(profile.short_yardage_trust)),
            int(round(profile.ball_security_mindset)),
            source,
            profile.notes,
        ]
    )
    return tuple(base)


def ensure_player_rb_behavior_schema(con) -> None:
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS player_rb_behavior_profiles (
            player_id INTEGER NOT NULL REFERENCES players(player_id) ON DELETE CASCADE,
            season INTEGER NOT NULL,
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
            source TEXT NOT NULL DEFAULT 'manual',
            notes TEXT,
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (player_id, season)
        );

        CREATE INDEX IF NOT EXISTS idx_player_rb_behavior_profiles_season
            ON player_rb_behavior_profiles(season, label);
        """
    )


def player_rb_behavior_table_exists(con) -> bool:
    row = con.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type = 'table'
          AND name = 'player_rb_behavior_profiles'
        """
    ).fetchone()
    return bool(row)


RB_STYLE_OVERRIDES: dict[str, RBBehaviorProfile] = {
    "aaron jones": profile(
        "Patient Zone Slasher",
        72,
        86,
        82,
        66,
        72,
        58,
        78,
        74,
        62,
        78,
        "Veteran pace, cutback vision, and receiving utility keep him efficient without pure power usage.",
    ),
    "aj dillon": profile("Rotational Power Back", 50, 60, 62, 32, 42, 86, 46, 36, 82, 70),
    "alvin kamara": profile("Receiving Space Back", 66, 82, 76, 68, 70, 56, 90, 92, 54, 78),
    "ashton jeanty": profile("Rookie Contact Creator", 84, 80, 82, 70, 82, 84, 78, 68, 80, 76),
    "austin ekeler": profile("Receiving Satellite Back", 54, 74, 72, 64, 62, 48, 84, 92, 42, 82),
    "bhayshul tuten": profile("Rookie Speed Back", 50, 58, 66, 78, 88, 48, 76, 58, 40, 62),
    "bijan robinson": profile("Creator Feature Back", 88, 84, 84, 74, 82, 74, 88, 78, 76, 76),
    "breece hall": profile("Explosive Patience Back", 78, 82, 78, 72, 86, 66, 80, 70, 66, 72),
    "brian robinson": profile("North-South Finisher", 72, 62, 78, 38, 52, 84, 52, 42, 84, 76),
    "bucky irving": profile("Compact Space Creator", 64, 76, 78, 76, 76, 56, 84, 74, 50, 72),
    "cam akers": profile("Urgent One-Cut Back", 48, 58, 72, 54, 60, 64, 56, 46, 58, 64),
    "chase brown": profile("Speed Volume Back", 74, 64, 74, 70, 82, 58, 76, 62, 56, 68),
    "chris rodriguez": profile("Short-Yardage Hammer", 42, 56, 66, 26, 38, 84, 38, 28, 88, 72),
    "christian mccaffrey": profile("Space Mismatch Workhorse", 92, 90, 86, 66, 78, 62, 96, 96, 70, 86),
    "chuba hubbard": profile("Decisive Volume Back", 76, 68, 82, 54, 66, 68, 62, 52, 72, 78),
    "dandre swift": profile("Space Slasher", 62, 72, 76, 76, 78, 50, 84, 76, 44, 70),
    "david montgomery": profile("Contact Finisher", 78, 74, 78, 36, 52, 88, 54, 48, 88, 82),
    "derrick henry": profile("Downhill Hammer", 90, 66, 82, 34, 76, 98, 46, 28, 98, 76),
    "devin singletary": profile("Patient Rotational Slasher", 54, 78, 76, 58, 58, 54, 68, 62, 52, 78),
    "devon achane": profile("Home-Run Space Back", 62, 62, 76, 86, 96, 42, 90, 76, 34, 66),
    "ezekiel elliott": profile("Veteran Short-Yardage Back", 48, 62, 68, 28, 34, 80, 42, 38, 86, 78),
    "gus edwards": profile("Downhill Role Back", 54, 54, 74, 24, 42, 86, 32, 24, 90, 76),
    "isiah pacheco": profile("Violent One-Cut Back", 76, 58, 84, 52, 70, 90, 58, 42, 86, 70),
    "jahmyr gibbs": profile("Perimeter Space Weapon", 76, 78, 84, 82, 90, 48, 94, 88, 42, 76),
    "james conner": profile("Contact Volume Back", 80, 70, 76, 32, 50, 90, 50, 46, 92, 78),
    "james cook": profile("Slashing Space Back", 72, 76, 82, 76, 82, 50, 86, 78, 46, 76),
    "jalen warren": profile("Contact Spark Back", 58, 70, 76, 62, 64, 82, 72, 68, 72, 72),
    "jamaal williams": profile("Veteran Goal-Line Back", 40, 58, 68, 24, 34, 84, 36, 28, 90, 80),
    "jaylen wright": profile("Developmental Home-Run Back", 44, 58, 68, 78, 86, 54, 72, 48, 46, 62),
    "jerome ford": profile("Rotational Perimeter Back", 48, 58, 68, 72, 76, 52, 68, 52, 48, 64),
    "jonathan taylor": profile("One-Cut Home-Run Back", 88, 76, 90, 60, 88, 78, 68, 46, 86, 78),
    "jordan mason": profile("Downhill Rotational Hammer", 64, 58, 78, 30, 48, 88, 42, 30, 88, 72),
    "josh jacobs": profile("Volume Contact Back", 88, 76, 80, 48, 62, 90, 62, 58, 92, 78),
    "kareem hunt": profile("Contact Passing-Down Back", 54, 72, 70, 42, 46, 80, 64, 70, 78, 78),
    "kenneth walker": profile("Bounce Explosive Runner", 74, 58, 74, 88, 88, 64, 76, 48, 58, 66),
    "kyren williams": profile("Decisive Workhorse", 88, 78, 88, 44, 60, 78, 66, 66, 82, 84),
    "najee harris": profile("Volume Power Back", 78, 66, 72, 44, 48, 88, 58, 58, 88, 78),
    "nick chubb": profile("Efficient Downhill Veteran", 82, 80, 88, 40, 68, 92, 50, 30, 92, 80),
    "omarion hampton": profile("Rookie Power Feature", 76, 70, 78, 48, 68, 86, 62, 50, 86, 72),
    "quinshon judkins": profile("Rookie Downhill Finisher", 72, 66, 78, 42, 62, 88, 54, 42, 88, 72),
    "rachaad white": profile("Receiving Volume Back", 72, 78, 74, 58, 58, 56, 80, 86, 58, 80),
    "raheem mostert": profile("Veteran Speed Back", 52, 62, 78, 72, 86, 54, 72, 42, 50, 70),
    "rhamondre stevenson": profile("Power Receiving Back", 74, 72, 72, 42, 52, 86, 70, 72, 84, 72),
    "rico dowdle": profile("Physical Volume Back", 70, 66, 76, 46, 58, 80, 58, 50, 78, 72),
    "saquon barkley": profile("Explosive Feature Back", 94, 82, 86, 76, 92, 82, 86, 72, 84, 78),
    "tony pollard": profile("One-Cut Space Back", 70, 72, 82, 72, 80, 56, 80, 70, 54, 72),
    "travis etienne": profile("Perimeter Burst Back", 78, 70, 80, 76, 84, 54, 82, 68, 54, 70),
    "treveyon henderson": profile("Rookie Space Sprinter", 62, 72, 82, 78, 88, 52, 88, 78, 46, 76),
    "tyjae spears": profile("Space Committee Back", 52, 70, 76, 76, 74, 48, 82, 76, 40, 70),
    "tyrone tracy": profile("Converted Space Back", 58, 64, 74, 74, 72, 58, 80, 68, 48, 64),
    "zach charbonnet": profile("Contact Complement", 58, 66, 74, 38, 48, 84, 58, 56, 84, 74),
    "zamir white": profile("Straight-Line Power Back", 50, 48, 72, 30, 54, 82, 42, 28, 82, 68),
}


class RatingProfileSource:
    def __init__(self, ratings: dict[str, int | float], name: str = "Generated RB", position: str = "RB") -> None:
        self.name = name
        self.position = position
        self.ratings = ratings

    def rating(self, key: str, default: float = 50.0) -> float:
        return float(self.ratings.get(key, default))


def infer_profile(rb: PlayerSnapshot) -> RBBehaviorProfile:
    mental = average(
        [
            rb.rating("play_recognition"),
            rb.rating("processing_speed"),
            rb.rating("composure"),
            rb.rating("consistency"),
        ]
    )
    athletic = average(
        [
            rb.rating("speed"),
            rb.rating("acceleration"),
            rb.rating("agility"),
            rb.rating("elusiveness"),
        ]
    )
    receiving = average(
        [
            rb.rating("hands"),
            rb.rating("route_timing"),
            rb.rating("catch_in_traffic"),
            rb.rating("release_vs_press"),
        ]
    )
    vision = rb.rating("carry_vision")
    patience_rating = rb.rating("run_patience")
    power = rb.rating("contact_power")
    balance = rb.rating("balance")
    security = rb.rating("ball_security")
    position = str(getattr(rb, "position", "RB")).upper()

    patience = clamp_rating(patience_rating * 0.62 + vision * 0.28 + mental * 0.10)
    one_cut = clamp_rating(vision * 0.36 + rb.rating("acceleration") * 0.24 + rb.rating("agility") * 0.20 + patience_rating * 0.20)
    bounce = clamp_rating(50 + (rb.rating("speed") - 70) * 0.34 + (rb.rating("agility") - 68) * 0.26 + (rb.rating("elusiveness") - 68) * 0.24 - (power - 68) * 0.12)
    home_run = clamp_rating(rb.rating("speed") * 0.38 + rb.rating("acceleration") * 0.24 + rb.rating("elusiveness") * 0.22 + vision * 0.16)
    contact = clamp_rating(power * 0.46 + rb.rating("strength") * 0.18 + balance * 0.26 + rb.rating("composure") * 0.10)
    space = clamp_rating(rb.rating("elusiveness") * 0.34 + rb.rating("agility") * 0.24 + receiving * 0.22 + rb.rating("speed") * 0.20)
    pass_usage = clamp_rating(receiving * 0.48 + rb.rating("hands") * 0.22 + rb.rating("route_timing") * 0.16 + rb.rating("pass_block_power") * 0.07 + rb.rating("pass_block_finesse") * 0.07)
    short_yardage = clamp_rating(contact * 0.38 + balance * 0.25 + security * 0.20 + rb.rating("strength") * 0.17)
    early_down = clamp_rating(
        vision * 0.24
        + contact * 0.19
        + one_cut * 0.18
        + short_yardage * 0.16
        + security * 0.13
        + rb.rating("stamina") * 0.10
    )
    security_mindset = clamp_rating(security * 0.64 + rb.rating("composure") * 0.20 + rb.rating("consistency") * 0.16)

    if position == "FB":
        label = "Inferred Utility Fullback"
        early_down = clamp_rating(early_down - 14)
        pass_usage = clamp_rating(pass_usage - 6)
        short_yardage = clamp_rating(short_yardage + 8)
        contact = clamp_rating(contact + 8)
        bounce = clamp_rating(bounce - 10)
        home_run = clamp_rating(home_run - 8)
    elif contact >= 78 and short_yardage >= 78:
        label = "Inferred Power Finisher"
    elif pass_usage >= 76 and space >= 74:
        label = "Inferred Receiving Back"
    elif home_run >= 78 and bounce >= 70:
        label = "Inferred Home-Run Back"
    elif one_cut >= 76 and patience >= 72:
        label = "Inferred One-Cut Back"
    elif early_down >= 76:
        label = "Inferred Feature Back"
    else:
        label = "Inferred Balanced Back"

    return RBBehaviorProfile(
        label=label,
        early_down_gravity=early_down,
        patience=patience,
        one_cut_decisiveness=one_cut,
        bounce_tendency=bounce,
        home_run_hunting=home_run,
        contact_appetite=contact,
        space_creation=space,
        pass_game_usage=pass_usage,
        short_yardage_trust=short_yardage,
        ball_security_mindset=security_mindset,
        notes="Inferred from current RB/FB ratings.",
    )


def with_deltas(base: RBBehaviorProfile, *, label: str, notes: str, **deltas: float) -> RBBehaviorProfile:
    values = base.as_dict()
    for field in RB_BEHAVIOR_FIELDS:
        values[field] = clamp(float(values[field]) + float(deltas.get(field, 0.0)), 1, 99)
    values["label"] = label
    values["notes"] = notes
    return profile_from_mapping(values)


def generated_rb_behavior_profile(
    archetype: str,
    ratings: dict[str, int | float],
    *,
    position: str = "RB",
) -> RBBehaviorProfile:
    base = infer_profile(RatingProfileSource(ratings, position=position))
    archetype = str(archetype or "").strip()
    if archetype == "Elusive back":
        return with_deltas(
            base,
            label="Generated Elusive Back",
            notes="Generated from draft RB archetype and true ratings.",
            early_down_gravity=-4,
            one_cut_decisiveness=5,
            bounce_tendency=12,
            home_run_hunting=8,
            contact_appetite=-10,
            space_creation=10,
            pass_game_usage=4,
            short_yardage_trust=-12,
        )
    if archetype == "Power back":
        return with_deltas(
            base,
            label="Generated Power Back",
            notes="Generated from draft RB archetype and true ratings.",
            early_down_gravity=8,
            patience=-2,
            one_cut_decisiveness=3,
            bounce_tendency=-14,
            home_run_hunting=-6,
            contact_appetite=14,
            space_creation=-8,
            pass_game_usage=-8,
            short_yardage_trust=14,
            ball_security_mindset=4,
        )
    if archetype == "Receiving back":
        return with_deltas(
            base,
            label="Generated Receiving Back",
            notes="Generated from draft RB archetype and true ratings.",
            early_down_gravity=-10,
            patience=4,
            bounce_tendency=8,
            home_run_hunting=4,
            contact_appetite=-12,
            space_creation=14,
            pass_game_usage=18,
            short_yardage_trust=-16,
            ball_security_mindset=6,
        )
    if archetype == "One-cut back":
        return with_deltas(
            base,
            label="Generated One-Cut Back",
            notes="Generated from draft RB archetype and true ratings.",
            early_down_gravity=6,
            patience=8,
            one_cut_decisiveness=14,
            bounce_tendency=-6,
            home_run_hunting=3,
            contact_appetite=2,
            short_yardage_trust=4,
            ball_security_mindset=5,
        )
    if archetype == "Lead blocker":
        return with_deltas(
            base,
            label="Generated Lead Fullback",
            notes="Generated from draft FB archetype and true ratings.",
            early_down_gravity=-20,
            patience=-4,
            one_cut_decisiveness=-8,
            bounce_tendency=-16,
            home_run_hunting=-18,
            contact_appetite=16,
            space_creation=-10,
            pass_game_usage=-12,
            short_yardage_trust=8,
            ball_security_mindset=6,
        )
    if archetype == "H-back":
        return with_deltas(
            base,
            label="Generated H-Back",
            notes="Generated from draft FB archetype and true ratings.",
            early_down_gravity=-18,
            patience=2,
            bounce_tendency=-8,
            home_run_hunting=-10,
            contact_appetite=8,
            space_creation=6,
            pass_game_usage=8,
            short_yardage_trust=2,
            ball_security_mindset=4,
        )
    if archetype == "Short-yardage back":
        return with_deltas(
            base,
            label="Generated Short-Yardage Back",
            notes="Generated from draft FB/RB archetype and true ratings.",
            early_down_gravity=-8,
            patience=-4,
            one_cut_decisiveness=2,
            bounce_tendency=-18,
            home_run_hunting=-16,
            contact_appetite=18,
            space_creation=-14,
            pass_game_usage=-14,
            short_yardage_trust=20,
            ball_security_mindset=8,
        )
    if archetype == "Move fullback":
        return with_deltas(
            base,
            label="Generated Move Fullback",
            notes="Generated from draft FB archetype and true ratings.",
            early_down_gravity=-18,
            patience=2,
            bounce_tendency=-4,
            home_run_hunting=-8,
            contact_appetite=6,
            space_creation=10,
            pass_game_usage=12,
            short_yardage_trust=0,
            ball_security_mindset=4,
        )
    return with_deltas(
        base,
        label="Generated Balanced RB",
        notes="Generated from draft RB/FB true ratings.",
    )


def metadata_profile(rb) -> RBBehaviorProfile | None:
    metadata = getattr(rb, "metadata", None) or {}
    stored = metadata.get("rb_behavior_profile")
    if isinstance(stored, RBBehaviorProfile):
        return stored
    if isinstance(stored, dict):
        return profile_from_mapping(stored)
    return None


def metadata_source(rb) -> str | None:
    metadata = getattr(rb, "metadata", None) or {}
    source = metadata.get("rb_behavior_source")
    return str(source) if source is not None else None


def rb_behavior_source(rb) -> str:
    stored = metadata_profile(rb)
    stored_source = metadata_source(rb)
    if stored and stored_source != "rb_behavior_named_seed":
        return "stored"
    if normalize_name(rb.name) in RB_STYLE_OVERRIDES:
        return "named"
    if stored:
        return "stored"
    return "inferred"


def rb_behavior_profile(rb: PlayerSnapshot) -> RBBehaviorProfile:
    stored = metadata_profile(rb)
    stored_source = metadata_source(rb)
    if stored and stored_source != "rb_behavior_named_seed":
        return stored
    named = RB_STYLE_OVERRIDES.get(normalize_name(rb.name))
    if named:
        return named
    if stored:
        return stored
    return infer_profile(rb)
