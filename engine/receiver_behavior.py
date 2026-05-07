"""Receiver style profiles for sim and tick-level play behavior.

These are football behavior traits, not hidden personality traits. They give
the resolver a way to make WRs and TEs with similar ratings play differently.
All values are 0-100, where 50 is neutral.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ReceiverBehaviorProfile:
    label: str
    target_gravity: float
    release_urgency: float
    route_pacing: float
    vertical_intent: float
    middle_comfort: float
    contested_alpha: float
    sideline_awareness: float
    yac_intent: float
    scramble_drill: float
    catch_security: float
    notes: str = ""

    def as_dict(self) -> dict[str, float | str]:
        return {
            "label": self.label,
            "target_gravity": round(self.target_gravity, 1),
            "release_urgency": round(self.release_urgency, 1),
            "route_pacing": round(self.route_pacing, 1),
            "vertical_intent": round(self.vertical_intent, 1),
            "middle_comfort": round(self.middle_comfort, 1),
            "contested_alpha": round(self.contested_alpha, 1),
            "sideline_awareness": round(self.sideline_awareness, 1),
            "yac_intent": round(self.yac_intent, 1),
            "scramble_drill": round(self.scramble_drill, 1),
            "catch_security": round(self.catch_security, 1),
            "notes": self.notes,
        }


RECEIVER_BEHAVIOR_FIELDS = (
    "target_gravity",
    "release_urgency",
    "route_pacing",
    "vertical_intent",
    "middle_comfort",
    "contested_alpha",
    "sideline_awareness",
    "yac_intent",
    "scramble_drill",
    "catch_security",
)


def profile(
    label: str,
    target: int,
    release: int,
    route: int,
    vertical: int,
    middle: int,
    contested: int,
    sideline: int,
    yac: int,
    scramble: int,
    security: int,
    notes: str = "",
) -> ReceiverBehaviorProfile:
    return ReceiverBehaviorProfile(
        label=label,
        target_gravity=target,
        release_urgency=release,
        route_pacing=route,
        vertical_intent=vertical,
        middle_comfort=middle,
        contested_alpha=contested,
        sideline_awareness=sideline,
        yac_intent=yac,
        scramble_drill=scramble,
        catch_security=security,
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


def profile_from_mapping(values: dict[str, Any]) -> ReceiverBehaviorProfile:
    return ReceiverBehaviorProfile(
        label=str(values.get("label") or "Stored Receiver Profile"),
        target_gravity=float(values.get("target_gravity", 50)),
        release_urgency=float(values.get("release_urgency", 50)),
        route_pacing=float(values.get("route_pacing", 50)),
        vertical_intent=float(values.get("vertical_intent", 50)),
        middle_comfort=float(values.get("middle_comfort", 50)),
        contested_alpha=float(values.get("contested_alpha", 50)),
        sideline_awareness=float(values.get("sideline_awareness", 50)),
        yac_intent=float(values.get("yac_intent", 50)),
        scramble_drill=float(values.get("scramble_drill", 50)),
        catch_security=float(values.get("catch_security", 50)),
        notes=str(values.get("notes") or ""),
    )


def profile_to_db_tuple(player_or_prospect_id: int, season: int | None, profile: ReceiverBehaviorProfile, source: str):
    base = [
        int(player_or_prospect_id),
    ]
    if season is not None:
        base.append(int(season))
    base.extend(
        [
            profile.label,
            int(round(profile.target_gravity)),
            int(round(profile.release_urgency)),
            int(round(profile.route_pacing)),
            int(round(profile.vertical_intent)),
            int(round(profile.middle_comfort)),
            int(round(profile.contested_alpha)),
            int(round(profile.sideline_awareness)),
            int(round(profile.yac_intent)),
            int(round(profile.scramble_drill)),
            int(round(profile.catch_security)),
            source,
            profile.notes,
        ]
    )
    return tuple(base)


def ensure_player_receiver_behavior_schema(con) -> None:
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS player_receiver_behavior_profiles (
            player_id INTEGER NOT NULL REFERENCES players(player_id) ON DELETE CASCADE,
            season INTEGER NOT NULL,
            label TEXT NOT NULL,
            target_gravity INTEGER NOT NULL CHECK (target_gravity BETWEEN 0 AND 100),
            release_urgency INTEGER NOT NULL CHECK (release_urgency BETWEEN 0 AND 100),
            route_pacing INTEGER NOT NULL CHECK (route_pacing BETWEEN 0 AND 100),
            vertical_intent INTEGER NOT NULL CHECK (vertical_intent BETWEEN 0 AND 100),
            middle_comfort INTEGER NOT NULL CHECK (middle_comfort BETWEEN 0 AND 100),
            contested_alpha INTEGER NOT NULL CHECK (contested_alpha BETWEEN 0 AND 100),
            sideline_awareness INTEGER NOT NULL CHECK (sideline_awareness BETWEEN 0 AND 100),
            yac_intent INTEGER NOT NULL CHECK (yac_intent BETWEEN 0 AND 100),
            scramble_drill INTEGER NOT NULL CHECK (scramble_drill BETWEEN 0 AND 100),
            catch_security INTEGER NOT NULL CHECK (catch_security BETWEEN 0 AND 100),
            source TEXT NOT NULL DEFAULT 'manual',
            notes TEXT,
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (player_id, season)
        );

        CREATE INDEX IF NOT EXISTS idx_player_receiver_behavior_profiles_season
            ON player_receiver_behavior_profiles(season, label);
        """
    )


def player_receiver_behavior_table_exists(con) -> bool:
    row = con.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type = 'table'
          AND name = 'player_receiver_behavior_profiles'
        """
    ).fetchone()
    return bool(row)


RECEIVER_STYLE_OVERRIDES: dict[str, ReceiverBehaviorProfile] = {
    "aj brown": profile("Power Alpha Receiver", 92, 78, 80, 74, 78, 92, 82, 88, 82, 82),
    "amonra st brown": profile("Slot Volume Engine", 94, 86, 94, 44, 92, 72, 80, 78, 88, 92),
    "brian thomas": profile("Explosive Boundary X", 78, 76, 72, 92, 60, 78, 82, 78, 70, 76),
    "brock bowers": profile("TE YAC Mismatch", 90, 82, 88, 66, 90, 82, 76, 92, 88, 88),
    "calvin ridley": profile("Tempo Route Separator", 74, 82, 84, 72, 68, 58, 78, 64, 74, 76),
    "ceedee lamb": profile("Moveable Volume Alpha", 96, 88, 92, 76, 88, 78, 84, 86, 88, 88),
    "chris godwin": profile("Middle Volume Receiver", 82, 76, 88, 52, 90, 70, 78, 74, 84, 90),
    "chris olave": profile("Smooth Vertical Separator", 78, 84, 86, 84, 66, 58, 86, 66, 78, 80),
    "cooper kupp": profile("Option-Route Technician", 88, 80, 96, 46, 94, 70, 78, 78, 92, 92),
    "courtland sutton": profile("Contested Boundary Target", 78, 64, 70, 70, 66, 92, 90, 58, 62, 78),
    "dalton kincaid": profile("Flex TE Separator", 78, 74, 84, 54, 88, 72, 70, 72, 82, 86),
    "dallas goedert": profile("Balanced In-Line Target", 74, 70, 78, 48, 84, 78, 70, 76, 78, 84),
    "davante adams": profile("Release Artist Alpha", 92, 98, 94, 72, 82, 86, 92, 62, 82, 90),
    "deebo samuel": profile("Manufactured YAC Weapon", 78, 72, 72, 48, 76, 76, 68, 96, 84, 76),
    "devonta smith": profile("Silky Boundary Separator", 84, 90, 90, 82, 76, 62, 90, 72, 82, 86),
    "dk metcalf": profile("Vertical Power X", 84, 78, 72, 92, 58, 88, 84, 82, 68, 78),
    "dj moore": profile("YAC Volume Receiver", 86, 78, 82, 66, 78, 72, 78, 90, 82, 82),
    "drake london": profile("Big Slot Alpha", 88, 72, 84, 62, 88, 94, 84, 72, 80, 86),
    "evan engram": profile("Move TE YAC Target", 76, 78, 82, 54, 84, 66, 68, 84, 80, 78),
    "garrett wilson": profile("Creative Separator Alpha", 88, 90, 88, 76, 82, 72, 86, 84, 88, 82),
    "george kittle": profile("Explosive Complete TE", 84, 76, 84, 58, 86, 84, 74, 94, 86, 86),
    "george pickens": profile("Boundary Ball-Winner", 80, 76, 68, 86, 56, 96, 92, 70, 68, 76),
    "jake ferguson": profile("Middle Chain-Mover TE", 72, 70, 78, 42, 88, 76, 68, 70, 76, 84),
    "jalen mcmillan": profile("Young Route Flasher", 62, 74, 76, 64, 66, 58, 70, 66, 72, 70),
    "jalen tolbert": profile("Vertical Complement", 58, 70, 68, 78, 52, 64, 76, 58, 62, 68),
    "jamar chase": profile("Explosive Alpha", 96, 90, 88, 92, 78, 86, 88, 92, 84, 86),
    "jameson williams": profile("Field-Stretch Volatility", 66, 76, 68, 96, 46, 54, 74, 82, 62, 62),
    "jaxon smithnjigba": profile("Slot Separator Volume", 84, 84, 92, 50, 88, 66, 78, 76, 86, 88),
    "jaylen waddle": profile("Speed Motion Separator", 82, 86, 84, 84, 70, 58, 78, 86, 78, 76),
    "jerry jeudy": profile("Route-Pace Separator", 74, 84, 86, 68, 70, 54, 76, 70, 78, 74),
    "jordan addison": profile("Boundary Route Finisher", 78, 82, 86, 74, 72, 66, 84, 70, 78, 82),
    "josh downs": profile("Slot Chain-Mover", 76, 84, 88, 42, 88, 58, 74, 76, 82, 82),
    "justin jefferson": profile(
        "Elite Route Alpha",
        98,
        92,
        96,
        82,
        88,
        86,
        92,
        82,
        90,
        92,
        "High-volume route technician who wins at every level and stays alive late in the down.",
    ),
    "kalif raymond": profile("Speed Utility Receiver", 56, 70, 70, 74, 58, 46, 66, 76, 68, 68),
    "keenan allen": profile("Veteran Option-Route Target", 84, 82, 94, 42, 92, 74, 78, 62, 86, 90),
    "kyle pitts": profile("Vertical Flex TE", 80, 78, 80, 78, 78, 82, 78, 72, 78, 82),
    "ladd mcconkey": profile("Rookie Slot Separator", 78, 86, 90, 62, 86, 58, 76, 76, 84, 84),
    "malik nabers": profile("Explosive Volume Creator", 92, 88, 86, 86, 80, 72, 84, 92, 86, 82),
    "mark andrews": profile("Red-Zone Middle TE", 84, 72, 86, 52, 92, 88, 76, 64, 82, 90),
    "marvin harrison": profile("Boundary Technician Alpha", 86, 86, 90, 84, 74, 82, 92, 68, 78, 86),
    "michael pittman": profile("Possession Boundary Target", 82, 72, 82, 54, 86, 86, 84, 70, 80, 88),
    "mike evans": profile("Vertical Red-Zone Alpha", 88, 78, 82, 88, 68, 94, 92, 58, 72, 86),
    "nico collins": profile("Explosive Big X", 88, 80, 82, 82, 76, 88, 86, 86, 78, 84),
    "puka nacua": profile("Physical Volume Mover", 92, 80, 90, 58, 92, 84, 82, 88, 90, 90),
    "rashee rice": profile("YAC Slot-Power Target", 80, 76, 80, 46, 84, 76, 72, 90, 82, 80),
    "rome odunze": profile("Rookie Boundary Alpha", 82, 78, 82, 80, 70, 88, 90, 70, 76, 84),
    "sam laporta": profile("Middle YAC TE", 84, 78, 86, 54, 90, 78, 74, 86, 84, 86),
    "stefon diggs": profile("Veteran Separation Alpha", 86, 88, 92, 66, 82, 72, 86, 72, 84, 88),
    "tee higgins": profile("Big Boundary Complement", 82, 72, 78, 78, 70, 92, 90, 58, 70, 82),
    "terry mclaurin": profile("Vertical Route Captain", 82, 86, 86, 84, 72, 68, 84, 74, 80, 84),
    "tj hockenson": profile("Volume Middle TE", 86, 72, 86, 48, 92, 82, 74, 70, 84, 88),
    "trey mcbride": profile("TE Volume Mismatch", 88, 76, 88, 50, 94, 82, 74, 80, 86, 90),
    "tyreek hill": profile("Speed Gravity Superstar", 96, 94, 88, 98, 72, 58, 82, 94, 82, 78),
    "xavier legette": profile("Power Slot Projection", 62, 70, 68, 70, 66, 78, 72, 80, 68, 68),
    "xavier worthy": profile("Pure Vertical Stress", 68, 86, 72, 99, 48, 46, 74, 88, 66, 66),
    "zay flowers": profile("Motion Slot Creator", 82, 90, 86, 68, 82, 52, 76, 90, 84, 78),
}


class RatingProfileSource:
    def __init__(self, ratings: dict[str, int | float], name: str = "Generated Receiver", position: str = "WR") -> None:
        self.name = name
        self.position = position
        self.ratings = ratings

    def rating(self, key: str, default: float = 50.0) -> float:
        return float(self.ratings.get(key, default))

    def role(self, _key: str, default: float = 50.0) -> float:
        return default


def infer_profile(receiver: PlayerSnapshot) -> ReceiverBehaviorProfile:
    position = str(getattr(receiver, "position", "WR")).upper()
    mental = average(
        [
            receiver.rating("play_recognition"),
            receiver.rating("processing_speed"),
            receiver.rating("composure"),
            receiver.rating("consistency"),
        ]
    )
    receiving = average(
        [
            receiver.rating("release_vs_press"),
            receiver.rating("route_snap"),
            receiver.rating("route_timing"),
            receiver.rating("hands"),
            receiver.rating("contested_catch"),
            receiver.rating("catch_in_traffic"),
        ]
    )
    role_score = max(
        float(getattr(receiver, "role", lambda _key, default=50.0: default)("boundary_wr", 50.0)),
        float(getattr(receiver, "role", lambda _key, default=50.0: default)("slot_wr", 50.0)),
        float(getattr(receiver, "role", lambda _key, default=50.0: default)("move_te", 50.0)),
        float(getattr(receiver, "role", lambda _key, default=50.0: default)("inline_te", 50.0)),
    )

    release = clamp_rating(receiver.rating("release_vs_press") * 0.54 + receiver.rating("route_snap") * 0.26 + receiver.rating("acceleration") * 0.20)
    route = clamp_rating(receiver.rating("route_timing") * 0.50 + mental * 0.22 + receiver.rating("route_snap") * 0.18 + receiver.rating("composure") * 0.10)
    vertical = clamp_rating(
        50
        + (receiver.rating("speed") - 70) * 0.36
        + (receiver.rating("acceleration") - 70) * 0.24
        + (receiver.rating("route_snap") - 68) * 0.16
        + (receiver.rating("contested_catch") - 65) * 0.14
    )
    middle = clamp_rating(
        receiver.rating("catch_in_traffic") * 0.34
        + receiver.rating("route_timing") * 0.24
        + receiver.rating("hands") * 0.16
        + receiver.rating("balance") * 0.14
        + receiver.rating("composure") * 0.12
    )
    contested = clamp_rating(
        receiver.rating("contested_catch") * 0.48
        + receiver.rating("catch_in_traffic") * 0.20
        + receiver.rating("hands") * 0.16
        + receiver.rating("strength") * 0.08
        + receiver.rating("balance") * 0.08
    )
    sideline = clamp_rating(
        receiver.rating("route_timing") * 0.30
        + receiver.rating("hands") * 0.20
        + receiver.rating("contested_catch") * 0.16
        + receiver.rating("agility") * 0.14
        + receiver.rating("composure") * 0.20
    )
    yac = clamp_rating(
        receiver.rating("elusiveness") * 0.32
        + receiver.rating("speed") * 0.22
        + receiver.rating("agility") * 0.20
        + receiver.rating("balance") * 0.12
        + receiver.rating("contact_power") * 0.08
        + receiver.rating("ball_security") * 0.06
    )
    scramble = clamp_rating(
        receiver.rating("play_recognition") * 0.25
        + receiver.rating("processing_speed") * 0.20
        + receiver.rating("agility") * 0.20
        + receiver.rating("route_snap") * 0.18
        + receiver.rating("composure") * 0.17
    )
    security = clamp_rating(
        receiver.rating("hands") * 0.42
        + receiver.rating("catch_in_traffic") * 0.20
        + receiver.rating("ball_security") * 0.20
        + receiver.rating("composure") * 0.10
        + receiver.rating("consistency") * 0.08
    )
    target = clamp_rating(
        receiving * 0.30
        + role_score * 0.22
        + route * 0.16
        + contested * 0.10
        + vertical * 0.08
        + middle * 0.08
        + security * 0.06
    )

    if position == "TE":
        vertical = clamp_rating(vertical - 6)
        middle = clamp_rating(middle + 6)
        contested = clamp_rating(contested + 5)
        if target >= 78 and middle >= 82:
            label = "Inferred TE Mismatch"
        elif contested >= 78:
            label = "Inferred Chain-Moving TE"
        else:
            label = "Inferred Inline Outlet"
    elif target >= 82 and route >= 78:
        label = "Inferred Volume Separator"
    elif vertical >= 80 and target >= 72:
        label = "Inferred Vertical Target"
    elif contested >= 80:
        label = "Inferred Contested Target"
    elif middle >= 80 and route >= 76:
        label = "Inferred Middle Mover"
    elif yac >= 80:
        label = "Inferred YAC Creator"
    else:
        label = "Inferred Balanced Receiver"

    return ReceiverBehaviorProfile(
        label=label,
        target_gravity=target,
        release_urgency=release,
        route_pacing=route,
        vertical_intent=vertical,
        middle_comfort=middle,
        contested_alpha=contested,
        sideline_awareness=sideline,
        yac_intent=yac,
        scramble_drill=scramble,
        catch_security=security,
        notes="Inferred from current WR/TE ratings.",
    )


def with_deltas(base: ReceiverBehaviorProfile, *, label: str, notes: str, **deltas: float) -> ReceiverBehaviorProfile:
    values = base.as_dict()
    for field in RECEIVER_BEHAVIOR_FIELDS:
        values[field] = clamp(float(values[field]) + float(deltas.get(field, 0.0)), 1, 99)
    values["label"] = label
    values["notes"] = notes
    return profile_from_mapping(values)


def generated_receiver_behavior_profile(
    archetype: str,
    ratings: dict[str, int | float],
    *,
    position: str = "WR",
) -> ReceiverBehaviorProfile:
    base = infer_profile(RatingProfileSource(ratings, position=position))
    archetype = str(archetype or "").strip()
    if archetype == "Vertical threat":
        return with_deltas(
            base,
            label="Generated Vertical Threat",
            notes="Generated from draft WR archetype and true ratings.",
            release_urgency=5,
            route_pacing=-4,
            vertical_intent=16,
            middle_comfort=-10,
            contested_alpha=-4,
            sideline_awareness=6,
            yac_intent=4,
            scramble_drill=-4,
        )
    if archetype == "Slot separator":
        return with_deltas(
            base,
            label="Generated Slot Separator",
            notes="Generated from draft WR archetype and true ratings.",
            target_gravity=4,
            release_urgency=10,
            route_pacing=12,
            vertical_intent=-12,
            middle_comfort=12,
            contested_alpha=-10,
            sideline_awareness=-2,
            yac_intent=6,
            scramble_drill=10,
            catch_security=4,
        )
    if archetype == "Possession target":
        return with_deltas(
            base,
            label="Generated Possession Target",
            notes="Generated from draft WR archetype and true ratings.",
            target_gravity=8,
            release_urgency=-2,
            route_pacing=10,
            vertical_intent=-14,
            middle_comfort=12,
            contested_alpha=6,
            sideline_awareness=6,
            yac_intent=-6,
            scramble_drill=6,
            catch_security=12,
        )
    if archetype == "Contested-catch target":
        return with_deltas(
            base,
            label="Generated Contested Target",
            notes="Generated from draft WR archetype and true ratings.",
            target_gravity=8,
            release_urgency=-4,
            route_pacing=-2,
            vertical_intent=6,
            middle_comfort=4,
            contested_alpha=16,
            sideline_awareness=12,
            yac_intent=-8,
            scramble_drill=-2,
            catch_security=8,
        )
    if archetype == "Move tight end":
        return with_deltas(
            base,
            label="Generated Move TE",
            notes="Generated from draft TE archetype and true ratings.",
            target_gravity=6,
            release_urgency=8,
            route_pacing=10,
            vertical_intent=2,
            middle_comfort=10,
            contested_alpha=2,
            sideline_awareness=0,
            yac_intent=10,
            scramble_drill=8,
            catch_security=4,
        )
    if archetype == "Inline tight end":
        return with_deltas(
            base,
            label="Generated Inline TE Outlet",
            notes="Generated from draft TE archetype and true ratings.",
            target_gravity=-8,
            release_urgency=-6,
            route_pacing=0,
            vertical_intent=-14,
            middle_comfort=8,
            contested_alpha=8,
            sideline_awareness=-4,
            yac_intent=-4,
            scramble_drill=2,
            catch_security=8,
        )
    if archetype == "Mismatch target":
        return with_deltas(
            base,
            label="Generated TE Mismatch",
            notes="Generated from draft TE archetype and true ratings.",
            target_gravity=10,
            release_urgency=4,
            route_pacing=8,
            vertical_intent=8,
            middle_comfort=12,
            contested_alpha=14,
            sideline_awareness=4,
            yac_intent=4,
            scramble_drill=6,
            catch_security=6,
        )
    if archetype == "Blocking specialist":
        return with_deltas(
            base,
            label="Generated Blocking TE Outlet",
            notes="Generated from draft TE archetype and true ratings.",
            target_gravity=-18,
            release_urgency=-10,
            route_pacing=-6,
            vertical_intent=-18,
            middle_comfort=2,
            contested_alpha=4,
            sideline_awareness=-8,
            yac_intent=-10,
            scramble_drill=-4,
            catch_security=4,
        )
    return with_deltas(
        base,
        label="Generated Balanced Receiver",
        notes="Generated from draft WR/TE true ratings.",
    )


def metadata_profile(receiver) -> ReceiverBehaviorProfile | None:
    metadata = getattr(receiver, "metadata", None) or {}
    stored = metadata.get("receiver_behavior_profile")
    if isinstance(stored, ReceiverBehaviorProfile):
        return stored
    if isinstance(stored, dict):
        return profile_from_mapping(stored)
    return None


def metadata_source(receiver) -> str | None:
    metadata = getattr(receiver, "metadata", None) or {}
    source = metadata.get("receiver_behavior_source")
    return str(source) if source is not None else None


def receiver_behavior_source(receiver) -> str:
    stored = metadata_profile(receiver)
    stored_source = metadata_source(receiver)
    if stored and stored_source != "receiver_behavior_named_seed":
        return "stored"
    if normalize_name(receiver.name) in RECEIVER_STYLE_OVERRIDES:
        return "named"
    if stored:
        return "stored"
    return "inferred"


def receiver_behavior_profile(receiver: PlayerSnapshot) -> ReceiverBehaviorProfile:
    stored = metadata_profile(receiver)
    stored_source = metadata_source(receiver)
    if stored and stored_source != "receiver_behavior_named_seed":
        return stored
    named = RECEIVER_STYLE_OVERRIDES.get(normalize_name(receiver.name))
    if named:
        return named
    if stored:
        return stored
    return infer_profile(receiver)
