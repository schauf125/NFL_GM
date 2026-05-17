"""Generate normalized sim ratings for draft prospects.

This module deliberately targets the playable match-engine rating model instead
of the legacy ``players`` columns. The values generated here are the prospect's
internal true ratings; scouting fog can wrap them later without changing the
underlying sim profile.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

try:
    from database.setup_sim_ratings import RATING_DEFINITIONS, ROLE_WEIGHTS
except ModuleNotFoundError:  # pragma: no cover - defensive fallback for isolated imports.
    RATING_DEFINITIONS = []
    ROLE_WEIGHTS = {}


DEV_TRAIT_WEIGHTS: dict[str, dict[str, float]] = {
    "round_1": {"Normal": 55, "Star": 35, "Superstar": 6, "X-Factor": 4},
    "round_2_3": {"Normal": 74, "Star": 20, "Superstar": 3.5, "X-Factor": 2.5},
    "round_4_5": {"Normal": 93.0, "Star": 5, "Superstar": 1.2, "X-Factor": 0.8},
    "round_6_7": {"Normal": 96.7, "Star": 2.3, "Superstar": 0.7, "X-Factor": 0.3},
    "leftover": {"Normal": 98.9, "Star": 0.8, "Superstar": 0.2, "X-Factor": 0.1},
}

HIDDEN_UNLISTED_DEV_TRAIT_WEIGHTS = {
    "Normal": 97.8,
    "Star": 1.7,
    "Superstar": 0.4,
    "X-Factor": 0.1,
}

DEV_TRAIT_CEILING_BONUS = {
    "Normal": 0,
    "Star": 3,
    "Superstar": 5,
    "X-Factor": 7,
}

POSITION_BASELINES: dict[str, dict[str, float]] = {
    "QB": {"height": 74.0, "weight": 220.0, "speed": 66.0, "strength": 57.0, "agility": 65.0},
    "RB": {"height": 70.5, "weight": 215.0, "speed": 83.0, "strength": 60.0, "agility": 79.0},
    "FB": {"height": 72.0, "weight": 240.0, "speed": 69.0, "strength": 73.0, "agility": 64.0},
    "WR": {"height": 73.0, "weight": 202.0, "speed": 84.0, "strength": 50.0, "agility": 80.0},
    "TE": {"height": 76.5, "weight": 250.0, "speed": 70.0, "strength": 65.0, "agility": 66.0},
    "OT": {"height": 77.0, "weight": 315.0, "speed": 47.0, "strength": 76.0, "agility": 50.0},
    "OG": {"height": 76.0, "weight": 315.0, "speed": 44.0, "strength": 77.0, "agility": 49.0},
    "C": {"height": 75.0, "weight": 305.0, "speed": 42.0, "strength": 72.0, "agility": 46.0},
    "IDL": {"height": 75.5, "weight": 305.0, "speed": 56.0, "strength": 77.0, "agility": 56.0},
    "EDGE": {"height": 76.0, "weight": 260.0, "speed": 75.0, "strength": 69.0, "agility": 72.0},
    "OLB": {"height": 74.0, "weight": 240.0, "speed": 75.0, "strength": 64.0, "agility": 72.0},
    "ILB": {"height": 73.0, "weight": 235.0, "speed": 77.0, "strength": 64.0, "agility": 75.0},
    "CB": {"height": 71.0, "weight": 195.0, "speed": 85.0, "strength": 50.0, "agility": 83.0},
    "NB": {"height": 70.5, "weight": 193.0, "speed": 84.0, "strength": 49.0, "agility": 83.0},
    "FS": {"height": 72.0, "weight": 205.0, "speed": 80.0, "strength": 54.0, "agility": 78.0},
    "SS": {"height": 72.0, "weight": 210.0, "speed": 79.0, "strength": 57.0, "agility": 77.0},
    "K": {"height": 72.0, "weight": 200.0, "speed": 50.0, "strength": 50.0, "agility": 50.0},
    "P": {"height": 74.0, "weight": 210.0, "speed": 48.0, "strength": 50.0, "agility": 48.0},
    "LS": {"height": 74.0, "weight": 245.0, "speed": 50.0, "strength": 58.0, "agility": 50.0},
}

POSITION_ACTIVE_GROUPS: dict[str, set[str]] = {
    "QB": {"passer", "ball_carrier"},
    "RB": {"ball_carrier", "receiver", "blocker"},
    "FB": {"ball_carrier", "receiver", "blocker"},
    "WR": {"receiver", "ball_carrier", "blocker"},
    "TE": {"receiver", "blocker", "ball_carrier"},
    "OT": {"blocker"},
    "OG": {"blocker"},
    "C": {"blocker"},
    "IDL": {"pass_rusher", "run_defender", "tackler"},
    "EDGE": {"pass_rusher", "run_defender", "tackler"},
    "OLB": {"pass_rusher", "run_defender", "tackler", "coverage"},
    "ILB": {"run_defender", "coverage", "tackler", "pass_rusher"},
    "CB": {"coverage", "tackler", "ball_carrier"},
    "NB": {"coverage", "tackler", "ball_carrier"},
    "FS": {"coverage", "run_defender", "tackler", "ball_carrier"},
    "SS": {"coverage", "run_defender", "tackler", "ball_carrier"},
    "K": {"specialist"},
    "P": {"specialist"},
    "LS": {"blocker", "tackler"},
}

LOW_GROUP_CAPS: dict[str, float] = {
    "passer": 16,
    "ball_carrier": 22,
    "receiver": 18,
    "blocker": 18,
    "pass_rusher": 18,
    "run_defender": 18,
    "coverage": 18,
    "tackler": 18,
    "specialist": 18,
}

IDENTITY_ILLUSION_CAP = 3
IDENTITY_ILLUSION_CHANCE = 0.12
IDENTITY_CORE_FAIL_MARGIN = 8
IDENTITY_BETTER_FIT_MARGIN = 3.0

DISPLAY_NAMES = {
    key: display_name
    for key, display_name, _group, *_rest in RATING_DEFINITIONS
}
RATING_GROUPS = {
    key: group
    for key, _display_name, group, *_rest in RATING_DEFINITIONS
}

ARCHETYPE_CORE_TRAITS: dict[str, tuple[str, str, str]] = {
    "Pocket passer": ("pass_accuracy_mid", "processing_speed", "play_recognition"),
    "Rhythm passer": ("pass_accuracy_short", "throw_release", "processing_speed"),
    "Dual-threat": ("speed", "acceleration", "platform_control"),
    "Toolsy passer": ("throw_power", "platform_control", "acceleration"),
    "Elusive back": ("elusiveness", "agility", "acceleration"),
    "Power back": ("contact_power", "strength", "balance"),
    "Receiving back": ("hands", "route_timing", "elusiveness"),
    "One-cut back": ("carry_vision", "acceleration", "run_patience"),
    "Lead blocker": ("lead_block", "block_sustain", "strength"),
    "H-back": ("lead_block", "hands", "route_timing"),
    "Short-yardage back": ("contact_power", "strength", "ball_security"),
    "Move fullback": ("lead_block", "route_timing", "agility"),
    "Vertical threat": ("speed", "acceleration", "route_snap"),
    "Slot separator": ("route_snap", "route_timing", "agility"),
    "Possession target": ("hands", "route_timing", "catch_in_traffic"),
    "Contested-catch target": ("contested_catch", "catch_in_traffic", "hands"),
    "Move tight end": ("route_snap", "hands", "agility"),
    "Inline tight end": ("run_block_drive", "block_sustain", "pass_block_power"),
    "Mismatch target": ("hands", "contested_catch", "route_snap"),
    "Blocking specialist": ("run_block_drive", "pass_block_power", "block_sustain"),
    "Pass protector": ("pass_block_speed", "pass_block_finesse", "pass_block_power"),
    "Drive blocker": ("run_block_drive", "block_sustain", "strength"),
    "Zone mover": ("reach_block", "agility", "acceleration"),
    "Anchor blocker": ("pass_block_power", "strength", "balance"),
    "Speed rusher": ("speed", "acceleration", "speed_rush"),
    "Power edge": ("strength", "power_rush", "block_shedding"),
    "Hybrid linebacker": ("speed", "zone_coverage", "pursuit_angle"),
    "Run-setting edge": ("edge_contain", "gap_integrity", "block_shedding"),
    "Interior rusher": ("power_rush", "finesse_rush", "stunt_execution"),
    "Nose tackle": ("double_team_takeon", "strength", "gap_integrity"),
    "Gap penetrator": ("acceleration", "finesse_rush", "stunt_execution"),
    "Two-gapper": ("double_team_takeon", "block_shedding", "gap_integrity"),
    "Coverage linebacker": ("zone_coverage", "coverage_communication", "pursuit_angle"),
    "Box linebacker": ("run_diagnostics", "solo_tackle", "block_shedding"),
    "Blitzer": ("speed_rush", "power_rush", "pursuit_angle"),
    "Man corner": ("man_coverage", "press_coverage", "speed"),
    "Zone corner": ("zone_coverage", "zone_recovery", "play_recognition"),
    "Slot corner": ("agility", "man_coverage", "open_field_tackle"),
    "Deep safety": ("zone_coverage", "zone_recovery", "ball_skills"),
    "Versatile safety": ("zone_coverage", "open_field_tackle", "coverage_communication"),
    "Box safety": ("solo_tackle", "run_diagnostics", "hit_power"),
    "Accurate kicker": ("kick_accuracy", "composure", "discipline"),
    "Big-leg kicker": ("kick_power", "strength", "composure"),
    "Clutch kicker": ("kick_accuracy", "composure", "kick_power"),
    "Field-position punter": ("kick_accuracy", "kick_power", "composure"),
    "Big-leg punter": ("kick_power", "strength", "kick_accuracy"),
    "Directional punter": ("kick_accuracy", "discipline", "composure"),
    "Long snapper": ("block_sustain", "pass_block_power", "tackle_wrap"),
}

POSITION_ARCHETYPE_CANDIDATES: dict[str, tuple[str, ...]] = {
    "QB": ("Pocket passer", "Rhythm passer", "Dual-threat", "Toolsy passer"),
    "RB": ("Elusive back", "Power back", "Receiving back", "One-cut back"),
    "FB": ("Lead blocker", "H-back", "Short-yardage back", "Move fullback"),
    "WR": ("Vertical threat", "Slot separator", "Possession target", "Contested-catch target"),
    "TE": ("Move tight end", "Inline tight end", "Mismatch target", "Blocking specialist"),
    "OT": ("Pass protector", "Drive blocker", "Zone mover", "Anchor blocker"),
    "OG": ("Pass protector", "Drive blocker", "Zone mover", "Anchor blocker"),
    "C": ("Pass protector", "Drive blocker", "Zone mover", "Anchor blocker"),
    "EDGE": ("Speed rusher", "Power edge", "Hybrid linebacker", "Run-setting edge"),
    "OLB": ("Speed rusher", "Power edge", "Hybrid linebacker", "Run-setting edge"),
    "IDL": ("Interior rusher", "Nose tackle", "Gap penetrator", "Two-gapper"),
    "ILB": ("Coverage linebacker", "Box linebacker", "Blitzer"),
    "CB": ("Man corner", "Zone corner", "Slot corner"),
    "NB": ("Man corner", "Zone corner", "Slot corner"),
    "FS": ("Deep safety", "Versatile safety", "Box safety"),
    "SS": ("Box safety", "Versatile safety", "Deep safety"),
    "K": ("Accurate kicker", "Big-leg kicker", "Clutch kicker"),
    "P": ("Field-position punter", "Big-leg punter", "Directional punter"),
    "LS": ("Long snapper",),
}


@dataclass(frozen=True)
class ArchetypeIdentityResolution:
    archetype: str
    original_archetype: str
    status: str
    note: str


@dataclass(frozen=True)
class DraftProspectAttributes:
    """Normalized match-engine profile for a draft prospect."""

    true_grade: int
    ceiling_grade: int
    dev_trait: str
    risk_level: str
    archetype: str
    original_archetype: str
    archetype_identity_status: str
    archetype_identity_note: str
    primary_role: str
    secondary_role: str
    primary_role_score: float | None
    secondary_role_score: float | None
    ratings: dict[str, int]
    role_scores: dict[str, float]
    top_ratings: str
    weak_ratings: str


class DraftAttributeGenerator:
    """Generate true sim ratings and hidden role scores for draft prospects."""

    def __init__(self, *, seed: str | int | None = None) -> None:
        self.seed = seed
        self.rng = random.Random(seed)
        self.identity_illusion_count = 0

    def generate(
        self,
        *,
        position: str,
        rank: int,
        age: int,
        height_in: int,
        weight_lbs: int,
        arm_length_in: float | None = None,
        hand_size_in: float | None = None,
        handedness: str | None = None,
        class_strength: int = 50,
        talent_profile: str = "public_board",
    ) -> DraftProspectAttributes:
        position = position.upper()
        tier = rank_tier(rank)
        true_grade = self._true_grade(position, rank, tier, class_strength, age, talent_profile)
        dev_trait = self._dev_trait(tier, talent_profile=talent_profile)
        ceiling_grade = self._ceiling_grade(
            position,
            true_grade,
            dev_trait,
            age,
            tier,
            class_strength,
            rank,
            talent_profile,
        )
        original_archetype = self._archetype(position, height_in, weight_lbs, handedness)
        roles = self._roles(position, original_archetype)
        ratings = self._ratings(
            position=position,
            true_grade=true_grade,
            ceiling_grade=ceiling_grade,
            age=age,
            height_in=height_in,
            weight_lbs=weight_lbs,
            arm_length_in=arm_length_in,
            hand_size_in=hand_size_in,
            archetype=original_archetype,
        )
        identity = self._resolve_archetype_identity(
            position=position,
            original_archetype=original_archetype,
            ratings=ratings,
            true_grade=true_grade,
            ceiling_grade=ceiling_grade,
        )
        archetype = identity.archetype
        roles = self._roles(position, archetype)
        role_scores = self._role_scores(ratings, roles, true_grade)
        top_ratings = rating_summary(ratings, roles, position=position, highest=True)
        weak_ratings = rating_summary(ratings, roles, position=position, highest=False)
        risk_level = self._risk_level(true_grade, ceiling_grade, dev_trait, tier, rank)
        primary_role = roles[0] if roles else ""
        secondary_role = roles[1] if len(roles) > 1 else ""
        return DraftProspectAttributes(
            true_grade=true_grade,
            ceiling_grade=ceiling_grade,
            dev_trait=dev_trait,
            risk_level=risk_level,
            archetype=archetype,
            original_archetype=identity.original_archetype,
            archetype_identity_status=identity.status,
            archetype_identity_note=identity.note,
            primary_role=primary_role,
            secondary_role=secondary_role,
            primary_role_score=role_scores.get(primary_role) if primary_role else None,
            secondary_role_score=role_scores.get(secondary_role) if secondary_role else None,
            ratings=ratings,
            role_scores=role_scores,
            top_ratings=top_ratings,
            weak_ratings=weak_ratings,
        )

    def _true_grade(
        self,
        position: str,
        rank: int,
        tier: str,
        class_strength: int,
        age: int,
        talent_profile: str = "public_board",
    ) -> int:
        if position in {"K", "P"}:
            if rank <= 160:
                start, end, sigma = 76, 71, 3.2
                span_start, span_end = 97, 160
            elif rank <= 256:
                start, end, sigma = 73, 67, 3.4
                span_start, span_end = 161, 256
            else:
                start, end, sigma = 67, 58, 4.2
                span_start, span_end = 257, 330
            pct = 0.0 if span_end == span_start else (rank - span_start) / (span_end - span_start)
            base = start + ((end - start) * max(0.0, min(1.0, pct)))
            class_mod = (class_strength - 50) / 14.0
            outlier = 0.0
            if self.rng.random() < 0.10:
                outlier = self.rng.choice([-1, 1]) * self.rng.uniform(4.0, 9.0)
            raw_grade = (
                base
                + class_mod
                + self.rng.gauss(0, sigma)
                + outlier
                + self._talent_volatility(tier, talent_profile=talent_profile)
            )
            raw_grade += self._age_readiness_modifier(
                age=age,
                rank=rank,
                tier=tier,
                position=position,
                raw_grade=raw_grade,
                talent_profile=talent_profile,
            )
            if talent_profile == "hidden_unlisted":
                raw_grade = self._hidden_unlisted_true_grade(raw_grade)
            return clamp(raw_grade, 42, 82)

        if tier == "round_1":
            start, end, sigma = 75, 63, 4.4
            span_start, span_end = 1, 32
        elif tier == "round_2_3":
            start, end, sigma = 66, 58, 4.0
            span_start, span_end = 33, 96
        elif tier == "round_4_5":
            start, end, sigma = 60, 53, 4.1
            span_start, span_end = 97, 160
        elif tier == "round_6_7":
            start, end, sigma = 56, 48, 4.0
            span_start, span_end = 161, 256
        else:
            start, end, sigma = 52, 44, 4.0
            span_start, span_end = 257, 330

        pct = 0.0 if span_end == span_start else (rank - span_start) / (span_end - span_start)
        base = start + ((end - start) * max(0.0, min(1.0, pct)))
        class_mod = (class_strength - 50) / 10.0
        outlier = 0.0
        if self.rng.random() < 0.06:
            outlier = self.rng.choice([-1, 1]) * self.rng.uniform(3.0, 7.0)
        raw_grade = (
            base
            + class_mod
            + self.rng.gauss(0, sigma)
            + outlier
            + self._talent_volatility(tier, talent_profile=talent_profile)
        )
        raw_grade += self._age_readiness_modifier(
            age=age,
            rank=rank,
            tier=tier,
            position=position,
            raw_grade=raw_grade,
            talent_profile=talent_profile,
        )
        raw_grade += self._rookie_readiness_boost(
            position=position,
            rank=rank,
            tier=tier,
            talent_profile=talent_profile,
        )
        if talent_profile == "hidden_unlisted":
            raw_grade = self._hidden_unlisted_true_grade(raw_grade)
        return clamp(raw_grade, 35, 82)

    def _hidden_unlisted_true_grade(self, grade: float) -> float:
        grade += 1.8
        cap_roll = self.rng.random()
        if cap_roll < 0.025:
            cap = self.rng.uniform(68.0, 70.5)
            grade = max(grade, self.rng.uniform(66.0, 70.0))
        elif cap_roll < 0.055:
            cap = self.rng.uniform(64.0, 68.0)
            grade = max(grade, self.rng.uniform(61.0, 66.0))
        elif cap_roll < 0.28:
            cap = self.rng.uniform(60.0, 64.0)
        elif cap_roll < 0.74:
            cap = self.rng.uniform(56.0, 62.0)
        else:
            cap = self.rng.uniform(52.0, 58.0)
        if grade > cap:
            return cap - abs(self.rng.gauss(0.0, 0.9))
        return grade

    def _talent_volatility(self, tier: str, *, talent_profile: str = "public_board") -> float:
        if talent_profile == "hidden_unlisted":
            # Off-board players are usually late-round or priority-UDFA
            # profiles, but a few get the kind of weird late-discovery upside
            # that makes scouting fun.
            roll_value = self.rng.random()
            if roll_value < 0.10:
                return -self.rng.uniform(1.5, 6.5)
            if roll_value < 0.72:
                return self.rng.gauss(0.0, 2.5)
            if roll_value < 0.94:
                return self.rng.uniform(1.5, 5.5)
            if roll_value < 0.995:
                return self.rng.uniform(5.5, 10.0)
            return self.rng.uniform(10.0, 15.0)

        bust_chances = {
            "round_1": 0.26,
            "round_2_3": 0.16,
            "round_4_5": 0.13,
            "round_6_7": 0.11,
            "leftover": 0.09,
        }
        sleeper_chances = {
            "round_1": 0.02,
            "round_2_3": 0.05,
            "round_4_5": 0.07,
            "round_6_7": 0.08,
            "leftover": 0.06,
        }
        roll_value = self.rng.random()
        if roll_value < bust_chances[tier]:
            if tier == "round_1":
                return -self.rng.uniform(7.0, 16.0)
            return -self.rng.uniform(5.0, 13.0)
        if roll_value < bust_chances[tier] + sleeper_chances[tier]:
            return self.rng.uniform(3.0, 8.0)
        return 0.0

    def _age_readiness_modifier(
        self,
        *,
        age: int,
        rank: int,
        tier: str,
        position: str,
        raw_grade: float,
        talent_profile: str,
    ) -> float:
        """Shift current ability toward older floors and younger projection.

        This is intentionally a tendency, not a rule. A young blue-chip player
        can still be day-one ready, and an older player can still be a projection
        if the rest of the profile says so.
        """

        modifier = {
            20: -2.4,
            21: -1.3,
            22: 0.0,
            23: 0.9,
            24: 1.6,
            25: 2.1,
        }.get(age, 1.7 if age > 25 else 0.0)
        if age <= 21 and rank <= 12 and raw_grade >= 66:
            modifier *= 0.15
        elif age <= 21 and rank <= 32 and raw_grade >= 63:
            modifier *= 0.35
        elif age <= 21 and tier == "round_1" and raw_grade >= 60 and self.rng.random() < 0.22:
            modifier *= self.rng.uniform(0.05, 0.45)
        if age >= 23 and rank <= 16 and raw_grade >= 68:
            modifier *= 0.55
        if talent_profile == "hidden_unlisted":
            modifier *= 0.65
        if position in {"K", "P", "LS"}:
            modifier *= 0.35
        return modifier + self.rng.gauss(0.0, 0.65)

    def _rookie_readiness_boost(
        self,
        *,
        position: str,
        rank: int,
        tier: str,
        talent_profile: str,
    ) -> float:
        """Lift current rookie ability without turning every prospect into upside."""

        if position in {"K", "P", "LS"}:
            return 0.0
        if tier == "round_1":
            boost = 0.8
        elif tier == "round_2_3":
            boost = 1.5 if rank <= 64 else 3.0
        elif tier == "round_4_5":
            boost = 5.0 if rank <= 128 else 3.0
        elif tier == "round_6_7":
            boost = 1.5
        else:
            boost = 0.6

        if talent_profile == "hidden_unlisted":
            boost *= 0.45
        return boost

    def _ceiling_grade(
        self,
        position: str,
        true_grade: int,
        dev_trait: str,
        age: int,
        tier: str,
        class_strength: int,
        rank: int,
        talent_profile: str = "public_board",
    ) -> int:
        if position in {"K", "P"}:
            gap_base = 4 if rank <= 256 else 5
            class_mod = (class_strength - 50) / 18.0
            ceiling = (
                true_grade
                + gap_base
                + min(2, DEV_TRAIT_CEILING_BONUS[dev_trait])
                + self._age_ceiling_modifier(
                    age=age,
                    rank=rank,
                    true_grade=true_grade,
                    dev_trait=dev_trait,
                    tier=tier,
                    position=position,
                    talent_profile=talent_profile,
                )
                + class_mod
                + self.rng.gauss(0, 2.5)
            )
            if talent_profile == "hidden_unlisted":
                ceiling += self._hidden_ceiling_variance()
            return clamp(ceiling, max(48, true_grade + 1), 88)

        gap_base = {
            "round_1": 7,
            "round_2_3": 8,
            "round_4_5": 7,
            "round_6_7": 6,
            "leftover": 5,
        }[tier]
        class_mod = (class_strength - 50) / 12.0
        readiness_offset = self._rookie_readiness_boost(
            position=position,
            rank=rank,
            tier=tier,
            talent_profile=talent_profile,
        )
        ceiling = (
            true_grade
            + gap_base
            + DEV_TRAIT_CEILING_BONUS[dev_trait]
            + self._age_ceiling_modifier(
                age=age,
                rank=rank,
                true_grade=true_grade,
                dev_trait=dev_trait,
                tier=tier,
                position=position,
                talent_profile=talent_profile,
            )
            + class_mod
            + self.rng.gauss(0, 4.0)
            - (readiness_offset * 0.85)
        )
        if dev_trait == "X-Factor":
            ceiling = max(ceiling, 80 + self.rng.random() * 8)
        elif dev_trait == "Superstar":
            ceiling = max(ceiling, 78 + self.rng.random() * 6)
        elif dev_trait == "Star":
            ceiling = max(ceiling, 70 + self.rng.random() * 7)
        if rank <= 5 and true_grade >= 64 and self.rng.random() < 0.82:
            ceiling = max(ceiling, 80 + self.rng.random() * 5)
        elif rank <= 16 and true_grade >= 63 and self.rng.random() < 0.72:
            ceiling = max(ceiling, 77 + self.rng.random() * 5)
        elif rank <= 32 and true_grade >= 61 and self.rng.random() < 0.58:
            ceiling = max(ceiling, 73 + self.rng.random() * 5)
        elif rank <= 64 and true_grade >= 58 and self.rng.random() < 0.52:
            ceiling = max(ceiling, 68 + self.rng.random() * 5)
        if talent_profile == "hidden_unlisted":
            ceiling += self._hidden_ceiling_variance()
        ceiling = self._finished_product_ceiling(
            ceiling=ceiling,
            true_grade=true_grade,
            age=age,
            rank=rank,
            tier=tier,
            dev_trait=dev_trait,
            position=position,
            talent_profile=talent_profile,
        )
        return clamp(ceiling, max(45, true_grade + 1), 92)

    def _finished_product_ceiling(
        self,
        *,
        ceiling: float,
        true_grade: int,
        age: int,
        rank: int,
        tier: str,
        dev_trait: str,
        position: str,
        talent_profile: str,
    ) -> float:
        """Older rookies are usually closer to their ceiling, with exceptions.

        Seniors and graduates can still be late bloomers or raw traits bets,
        but the common case should be a more finished profile than a 20- or
        21-year-old early entrant.
        """

        if position in {"K", "P", "LS"} or age < 22:
            return ceiling
        gap = ceiling - true_grade
        if gap <= 1:
            return ceiling
        target_gap = {
            22: 6.8,
            23: 5.2,
            24: 4.2,
            25: 3.5,
        }.get(age, 3.2 if age > 25 else 6.8)
        if tier == "round_1":
            target_gap += 0.4
        elif tier == "round_2_3":
            target_gap += 0.2
        if rank <= 16 and true_grade >= 66:
            target_gap += 0.4
        if talent_profile == "hidden_unlisted":
            target_gap += 1.0

        exception_chance = {
            22: 0.18,
            23: 0.13,
            24: 0.09,
            25: 0.08,
        }.get(age, 0.06)
        if dev_trait == "X-Factor":
            exception_chance += 0.20
            target_gap += 1.4
        elif dev_trait == "Superstar":
            exception_chance += 0.14
            target_gap += 1.0
        elif dev_trait == "Star":
            exception_chance += 0.08
            target_gap += 0.5

        if gap <= target_gap or self.rng.random() < min(0.55, exception_chance):
            return ceiling
        return true_grade + max(1.0, target_gap + self.rng.gauss(0.0, 1.0))

    def _age_ceiling_modifier(
        self,
        *,
        age: int,
        rank: int,
        true_grade: int,
        dev_trait: str,
        tier: str,
        position: str,
        talent_profile: str,
    ) -> float:
        modifier = {
            20: 4.5,
            21: 3.2,
            22: 1.2,
            23: -0.9,
            24: -2.5,
            25: -3.8,
        }.get(age, -4.4 if age > 25 else 0.0)
        if age <= 21 and rank <= 16 and true_grade >= 67:
            modifier = max(modifier, 2.4)
        elif age <= 21 and rank <= 32 and true_grade >= 64:
            modifier = max(modifier, 1.6)
        if age >= 23 and rank <= 16 and true_grade >= 68:
            modifier = max(modifier, -1.2)
        elif age >= 24 and rank <= 32 and true_grade >= 65:
            modifier = max(modifier, -1.8)
        if dev_trait in {"Superstar", "X-Factor"}:
            modifier = modifier * 0.55 if modifier < 0 else modifier + 0.8
        elif dev_trait == "Star" and modifier < 0:
            modifier *= 0.75
        if talent_profile == "hidden_unlisted":
            modifier *= 1.25 if modifier < 0 else 0.85
        if position in {"K", "P", "LS"}:
            modifier *= 0.35
        return modifier + self.rng.gauss(0.0, 0.95)

    def _dev_trait(self, tier: str, *, talent_profile: str = "public_board") -> str:
        weights = HIDDEN_UNLISTED_DEV_TRAIT_WEIGHTS if talent_profile == "hidden_unlisted" else DEV_TRAIT_WEIGHTS[tier]
        traits = list(weights)
        values = [weights[trait] for trait in traits]
        return self.rng.choices(traits, weights=values, k=1)[0]

    def _hidden_ceiling_variance(self) -> float:
        roll_value = self.rng.random()
        if roll_value < 0.16:
            return self.rng.uniform(5.0, 13.0)
        if roll_value < 0.40:
            return -self.rng.uniform(1.0, 5.0)
        return self.rng.gauss(0.0, 2.2)

    def _archetype(
        self,
        position: str,
        height_in: int,
        weight_lbs: int,
        handedness: str | None,
    ) -> str:
        baseline = POSITION_BASELINES.get(position, POSITION_BASELINES["WR"])
        weight_delta = weight_lbs - baseline["weight"]
        height_delta = height_in - baseline["height"]
        if position == "QB":
            lefty = 1.5 if handedness == "Left" else 1.0
            return weighted_choice(
                self.rng,
                {
                    "Pocket passer": 35,
                    "Rhythm passer": 25,
                    "Dual-threat": 24 + max(0, -weight_delta / 8),
                    "Toolsy passer": 16 * lefty,
                },
            )
        if position == "RB":
            archetype = weighted_choice(
                self.rng,
                {
                    "Elusive back": 38 + max(0, -weight_delta / 5) - max(0, weight_delta - 15) / 1.5,
                    "Power back": 25 + max(0, weight_delta / 4) - max(0, -weight_delta - 8) / 2,
                    "Receiving back": 22 + max(0, -weight_delta / 10) - max(0, weight_delta - 12) / 1.5,
                    "One-cut back": 15,
                },
            )
            return self._fit_archetype_to_body(position, archetype, height_in, weight_lbs)
        if position == "FB":
            archetype = weighted_choice(
                self.rng,
                {
                    "Lead blocker": 42 + max(0, weight_delta / 8),
                    "H-back": 24 + max(0, height_delta * 2) + max(0, -weight_delta / 10),
                    "Short-yardage back": 20 + max(0, weight_delta / 7),
                    "Move fullback": 14 + max(0, -weight_delta / 6),
                },
            )
            return self._fit_archetype_to_body(position, archetype, height_in, weight_lbs)
        if position == "WR":
            archetype = weighted_choice(
                self.rng,
                {
                    "Vertical threat": 30,
                    "Slot separator": 25 + max(0, -height_delta * 5) - max(0, height_delta - 1) * 12,
                    "Possession target": 22,
                    "Contested-catch target": 18 + max(0, height_delta * 5) - max(0, 72 - height_in) * 12,
                },
            )
            return self._fit_archetype_to_body(position, archetype, height_in, weight_lbs)
        if position == "TE":
            archetype = weighted_choice(
                self.rng,
                {
                    "Move tight end": 35 + max(0, -weight_delta / 5) - max(0, weight_delta - 10) / 1.5,
                    "Inline tight end": 35 + max(0, weight_delta / 5),
                    "Mismatch target": 20 + max(0, -weight_delta / 12) - max(0, weight_delta - 15) / 2,
                    "Blocking specialist": 10 + max(0, weight_delta / 8),
                },
            )
            return self._fit_archetype_to_body(position, archetype, height_in, weight_lbs)
        if position in {"OT", "OG", "C"}:
            archetype = weighted_choice(
                self.rng,
                {
                    "Pass protector": 35 if position == "OT" else 20,
                    "Drive blocker": 35 + max(0, weight_delta / 8),
                    "Zone mover": 18 + max(0, -weight_delta / 8) - max(0, weight_delta - 12) / 1.5,
                    "Anchor blocker": 22 + max(0, weight_delta / 10) - max(0, -weight_delta - 8),
                },
            )
            return self._fit_archetype_to_body(position, archetype, height_in, weight_lbs)
        if position in {"EDGE", "OLB"}:
            archetype = weighted_choice(
                self.rng,
                {
                    "Speed rusher": 38 + max(0, -weight_delta / 8) - max(0, weight_delta - 10),
                    "Power edge": 30 + max(0, weight_delta / 7) - max(0, -weight_delta - 10),
                    "Hybrid linebacker": 16 + max(0, -weight_delta / 7) - max(0, weight_delta - 12) / 1.5,
                    "Run-setting edge": 16 + max(0, weight_delta / 9),
                },
            )
            return self._fit_archetype_to_body(position, archetype, height_in, weight_lbs)
        if position == "IDL":
            archetype = weighted_choice(
                self.rng,
                {
                    "Interior rusher": 35 + max(0, -weight_delta / 8) - max(0, weight_delta - 12) / 2,
                    "Nose tackle": 30 + max(0, weight_delta / 5) - max(0, -weight_delta - 8),
                    "Gap penetrator": 20 + max(0, -weight_delta / 12) - max(0, weight_delta - 12) / 1.5,
                    "Two-gapper": 15 + max(0, weight_delta / 8),
                },
            )
            return self._fit_archetype_to_body(position, archetype, height_in, weight_lbs)
        if position == "ILB":
            return weighted_choice(self.rng, {"Coverage linebacker": 38, "Box linebacker": 42, "Blitzer": 20})
        if position in {"CB", "NB"}:
            return weighted_choice(self.rng, {"Man corner": 44, "Zone corner": 35, "Slot corner": 21})
        if position == "FS":
            return weighted_choice(self.rng, {"Deep safety": 60, "Versatile safety": 25, "Box safety": 15})
        if position == "SS":
            return weighted_choice(self.rng, {"Box safety": 55, "Versatile safety": 30, "Deep safety": 15})
        if position == "K":
            return weighted_choice(self.rng, {"Accurate kicker": 48, "Big-leg kicker": 38, "Clutch kicker": 14})
        if position == "P":
            return weighted_choice(self.rng, {"Field-position punter": 45, "Big-leg punter": 40, "Directional punter": 15})
        if position == "LS":
            return "Long snapper"
        return "Balanced"

    def _fit_archetype_to_body(
        self,
        position: str,
        archetype: str,
        height_in: int,
        weight_lbs: int,
    ) -> str:
        if position == "FB" and archetype == "Balanced":
            return "Lead blocker"
        if position == "RB":
            if archetype in {"Elusive back", "Receiving back"} and weight_lbs >= 236:
                return weighted_choice(self.rng, {"Power back": 65, "One-cut back": 35})
            if archetype == "Power back" and weight_lbs < 205:
                return weighted_choice(self.rng, {"Elusive back": 60, "One-cut back": 40})
        if position == "WR":
            if archetype == "Slot separator" and height_in >= 76:
                return weighted_choice(self.rng, {"Contested-catch target": 55, "Vertical threat": 30, "Possession target": 15})
            if archetype == "Contested-catch target" and height_in < 71:
                return weighted_choice(self.rng, {"Slot separator": 60, "Possession target": 40})
        if position == "TE" and archetype in {"Move tight end", "Mismatch target"} and weight_lbs >= 270:
            return weighted_choice(self.rng, {"Inline tight end": 65, "Blocking specialist": 35})
        if position in {"OT", "OG", "C"}:
            if archetype == "Zone mover" and weight_lbs >= 330:
                return weighted_choice(self.rng, {"Drive blocker": 65, "Anchor blocker": 35})
            if archetype == "Anchor blocker" and weight_lbs < 300:
                return weighted_choice(self.rng, {"Pass protector": 60, "Zone mover": 40})
        if position in {"EDGE", "OLB"}:
            if archetype == "Speed rusher" and weight_lbs >= 285:
                return weighted_choice(self.rng, {"Power edge": 65, "Run-setting edge": 35})
            if archetype == "Power edge" and weight_lbs <= 235:
                return weighted_choice(self.rng, {"Hybrid linebacker": 60, "Speed rusher": 40})
        if position == "IDL":
            if archetype in {"Interior rusher", "Gap penetrator"} and weight_lbs >= 325:
                return weighted_choice(self.rng, {"Nose tackle": 60, "Two-gapper": 40})
            if archetype == "Nose tackle" and weight_lbs < 295:
                return weighted_choice(self.rng, {"Interior rusher": 55, "Gap penetrator": 45})
        return archetype

    def _roles(self, position: str, archetype: str) -> tuple[str, ...]:
        if position == "QB":
            return ("scrambling_qb", "pocket_qb") if archetype == "Dual-threat" else ("pocket_qb", "scrambling_qb")
        if position == "RB":
            return ("power_rb", "elusive_rb") if archetype == "Power back" else ("elusive_rb", "power_rb")
        if position == "FB":
            if archetype in {"H-back", "Move fullback"}:
                return ("move_te", "inline_te")
            if archetype == "Short-yardage back":
                return ("power_rb", "inline_te")
            return ("inline_te", "power_rb")
        if position == "WR":
            return ("slot_wr", "boundary_wr") if archetype == "Slot separator" else ("boundary_wr", "slot_wr")
        if position == "TE":
            return ("inline_te", "move_te") if archetype in {"Inline tight end", "Blocking specialist"} else ("move_te", "inline_te")
        if position == "OT":
            return ("pass_protecting_ot",)
        if position in {"OG", "C"}:
            return ("interior_run_blocker",)
        if position in {"EDGE", "OLB"}:
            return ("power_edge", "speed_edge") if archetype in {"Power edge", "Run-setting edge"} else ("speed_edge", "power_edge")
        if position == "IDL":
            return ("nose_run_stopping_dt", "interior_rusher") if archetype in {"Nose tackle", "Two-gapper"} else ("interior_rusher", "nose_run_stopping_dt")
        if position == "ILB":
            return ("coverage_lb", "box_lb") if archetype == "Coverage linebacker" else ("box_lb", "coverage_lb")
        if position in {"CB", "NB"}:
            return ("zone_cb", "man_cb") if archetype == "Zone corner" else ("man_cb", "zone_cb")
        if position == "FS":
            return ("deep_safety", "box_safety")
        if position == "SS":
            return ("box_safety", "deep_safety")
        return ()

    def _resolve_archetype_identity(
        self,
        *,
        position: str,
        original_archetype: str,
        ratings: dict[str, int],
        true_grade: int,
        ceiling_grade: int,
    ) -> ArchetypeIdentityResolution:
        original_score = archetype_identity_score(original_archetype, ratings)
        threshold = self._identity_threshold(true_grade)
        failed_traits = archetype_failed_core_traits(original_archetype, ratings, threshold)
        if len(failed_traits) < 2:
            return ArchetypeIdentityResolution(
                archetype=original_archetype,
                original_archetype=original_archetype,
                status="Aligned",
                note=f"Core identity traits support {original_archetype}.",
            )

        best_archetype, best_score = self._best_archetype_fit(position, ratings)
        if (
            self.identity_illusion_count < IDENTITY_ILLUSION_CAP
            and ceiling_grade - true_grade >= 10
            and self.rng.random() < IDENTITY_ILLUSION_CHANCE
        ):
            self.identity_illusion_count += 1
            return ArchetypeIdentityResolution(
                archetype=original_archetype,
                original_archetype=original_archetype,
                status="Illusion",
                note=(
                    f"Rare projection label kept despite weak core traits "
                    f"({', '.join(failed_traits)}); closest honest fit is {best_archetype}."
                ),
            )

        if best_archetype != original_archetype and best_score >= original_score + IDENTITY_BETTER_FIT_MARGIN:
            return ArchetypeIdentityResolution(
                archetype=best_archetype,
                original_archetype=original_archetype,
                status="Relabeled",
                note=(
                    f"Relabeled from {original_archetype} to {best_archetype}; "
                    f"core traits did not support original label ({', '.join(failed_traits)})."
                ),
            )

        return ArchetypeIdentityResolution(
            archetype=original_archetype,
            original_archetype=original_archetype,
            status="Thin",
            note=(
                f"Kept {original_archetype}, but core identity is thin "
                f"({', '.join(failed_traits)})."
            ),
        )

    def _best_archetype_fit(self, position: str, ratings: dict[str, int]) -> tuple[str, float]:
        candidates = POSITION_ARCHETYPE_CANDIDATES.get(position, ())
        if not candidates:
            return "Balanced", 0.0
        scored = [
            (candidate, archetype_identity_score(candidate, ratings))
            for candidate in candidates
        ]
        scored.sort(key=lambda item: item[1], reverse=True)
        return scored[0]

    @staticmethod
    def _identity_threshold(true_grade: int) -> int:
        return max(45, min(66, true_grade - IDENTITY_CORE_FAIL_MARGIN))

    def _ratings(
        self,
        *,
        position: str,
        true_grade: int,
        ceiling_grade: int,
        age: int,
        height_in: int,
        weight_lbs: int,
        arm_length_in: float | None,
        hand_size_in: float | None,
        archetype: str,
    ) -> dict[str, int]:
        baseline = POSITION_BASELINES.get(position, POSITION_BASELINES["WR"])
        weight_delta = weight_lbs - baseline["weight"]
        height_delta = height_in - baseline["height"]
        density_factor = 7.0 if position in {"FB", "TE", "OT", "OG", "C", "IDL", "EDGE", "OLB", "ILB", "LS"} else 5.0
        frame_density_delta = weight_delta - height_delta * density_factor
        arm_delta = (arm_length_in or (height_in * 0.44)) - (height_in * 0.44)
        hand_delta = (hand_size_in or 9.5) - 9.5

        athletic_bonus = (true_grade - 60) / 10
        speed = baseline["speed"] + athletic_bonus - max(0, weight_delta) / 9 + max(0, -weight_delta) / 14
        agility = baseline["agility"] + athletic_bonus - max(0, weight_delta) / 11 + max(0, -weight_delta) / 18
        strength = baseline["strength"] + weight_delta / 12 + frame_density_delta / 14 + max(0, height_delta) / 6

        if archetype in {"Vertical threat", "Speed rusher", "Elusive back", "Dual-threat", "Man corner", "Deep safety"}:
            speed += 3
            agility += 2
        if archetype in {"Power back", "Power edge", "Nose tackle", "Drive blocker", "Anchor blocker", "Box safety"}:
            strength += 4
            speed -= 1
        if archetype in {"Slot separator", "Zone mover", "Coverage linebacker", "Slot corner"}:
            agility += 3
        if archetype in {"Big-leg kicker", "Big-leg punter"}:
            strength += 3

        speed = roll(self.rng, speed, 4.0, 30, 99)
        agility = roll(self.rng, agility, 4.0, 30, 99)
        strength = roll(self.rng, strength, 4.0, 30, 99)
        acceleration = clamp(blend((speed, 0.55), (agility, 0.45)) + self.rng.gauss(0, 2.0), 25, 99)
        balance = clamp(blend((strength, 0.35), (agility, 0.25), (true_grade, 0.4)) + self.rng.gauss(0, 3.0), 25, 99)
        maturity = 1 if age >= 23 else 0
        maturity -= 1 if age <= 21 else 0
        recognition = clamp(true_grade - 2 + maturity * 2 + self.rng.gauss(0, 4.0), 30, 90)
        processing = clamp(blend((recognition, 0.6), (agility, 0.2), (true_grade, 0.2)) + self.rng.gauss(0, 2.0), 30, 92)
        discipline = clamp(true_grade - 1 + maturity * 2 + self.rng.gauss(0, 5.0), 28, 92)
        composure = clamp(true_grade + maturity * 2 + self.rng.gauss(0, 5.0), 28, 94)
        durability = clamp(66 + (strength - 55) * 0.15 - abs(weight_delta) * 0.03 + self.rng.gauss(0, 6.0), 35, 92)
        stamina = clamp(blend((true_grade, 0.45), (durability, 0.2), (strength if position in {"OT", "OG", "C", "IDL"} else speed, 0.2), (recognition, 0.15)), 35, 95)
        consistency = clamp(blend((true_grade, 0.55), (recognition, 0.25), (composure, 0.2)) + self.rng.gauss(0, 3.0), 30, 93)

        ratings = {
            "speed": speed,
            "acceleration": acceleration,
            "agility": agility,
            "balance": balance,
            "strength": strength,
            "play_recognition": recognition,
            "processing_speed": processing,
            "discipline": discipline,
            "composure": composure,
            "stamina": stamina,
            "durability": durability,
            "consistency": consistency,
        }

        ratings.update(self._skill_ratings(position, archetype, true_grade, speed, acceleration, agility, strength, balance, recognition, processing, composure, arm_delta, hand_delta))
        self._apply_body_skill_correlations(
            position=position,
            ratings=ratings,
            height_delta=height_delta,
            weight_delta=weight_delta,
            frame_density_delta=frame_density_delta,
            arm_delta=arm_delta,
            hand_delta=hand_delta,
        )
        return self._fill_and_cap(position, ratings)

    def _body_modifier(
        self,
        signal: float,
        scale: float,
        *,
        contrary_chance: float = 0.12,
        noise: float = 0.45,
        limit: float = 5.0,
    ) -> float:
        """Convert a physical signal into a rating nudge with rare reversals."""
        if abs(signal) < 0.01:
            return self.rng.gauss(0.0, noise * 0.35)
        modifier = signal * scale + self.rng.gauss(0.0, noise)
        if self.rng.random() < contrary_chance:
            modifier *= -self.rng.uniform(0.35, 0.95)
        return max(-limit, min(limit, modifier))

    def _nudge_rating(self, ratings: dict[str, int], key: str, amount: float) -> None:
        if key in ratings:
            ratings[key] = clamp(ratings[key] + amount, 1, 99)

    def _apply_body_skill_correlations(
        self,
        *,
        position: str,
        ratings: dict[str, int],
        height_delta: float,
        weight_delta: float,
        frame_density_delta: float,
        arm_delta: float,
        hand_delta: float,
    ) -> None:
        # Normalize around roughly meaningful football measurement differences.
        height_signal = max(-2.0, min(2.0, height_delta / 3.0))
        weight_signal = max(-2.0, min(2.0, weight_delta / 22.0))
        density_signal = max(-2.0, min(2.0, frame_density_delta / 24.0))
        arm_signal = max(-2.0, min(2.0, arm_delta / 1.15))
        hand_signal = max(-2.0, min(2.0, hand_delta / 0.55))
        anchor_signal = max(-2.0, min(2.0, density_signal * 0.88 + weight_signal * 0.12))
        power_frame_signal = max(-2.0, min(2.0, weight_signal * 0.65 + height_signal * 0.15 + density_signal * 0.20))

        hand_catch = self._body_modifier(hand_signal, 1.55, contrary_chance=0.14, limit=4.5)
        length_catch = self._body_modifier(height_signal * 0.55 + arm_signal * 0.45, 1.25, contrary_chance=0.10, limit=4.0)
        mass_power = self._body_modifier(power_frame_signal, 1.45, contrary_chance=0.11, limit=4.5)
        anchor_power = self._body_modifier(anchor_signal, 2.20, contrary_chance=0.10, limit=5.5)
        mass_mobility = self._body_modifier(weight_signal, -1.20, contrary_chance=0.13, limit=4.0)
        length_leverage = self._body_modifier(arm_signal, 1.15, contrary_chance=0.11, limit=3.5)

        if position in {"WR", "TE", "RB", "FB"}:
            for key in ("hands", "catch_in_traffic"):
                self._nudge_rating(ratings, key, hand_catch)
            self._nudge_rating(ratings, "contested_catch", hand_catch * 0.55 + length_catch * 1.25 + mass_power * 0.25)
            self._nudge_rating(ratings, "ball_security", hand_catch * 0.55 + mass_power * 0.20)
            self._nudge_rating(ratings, "release_vs_press", length_leverage * 0.45 + mass_power * 0.35)
            self._nudge_rating(ratings, "route_snap", mass_mobility * 0.65)
            self._nudge_rating(ratings, "elusiveness", mass_mobility * 0.65)
            self._nudge_rating(ratings, "contact_power", mass_power * 0.85)
        elif position == "QB":
            self._nudge_rating(ratings, "throw_power", hand_catch * 0.65 + mass_power * 0.35)
            self._nudge_rating(ratings, "ball_security", hand_catch * 0.45)
            self._nudge_rating(ratings, "platform_control", mass_power * 0.25 + mass_mobility * 0.25)
            self._nudge_rating(ratings, "throw_release", mass_mobility * 0.35)
        elif position in {"OT", "OG", "C"}:
            self._nudge_rating(ratings, "pass_block_power", anchor_power * 0.80 + mass_power * 0.20)
            self._nudge_rating(ratings, "run_block_drive", anchor_power * 0.70 + mass_power * 0.30)
            self._nudge_rating(ratings, "block_sustain", anchor_power * 0.50 + length_leverage * 0.35)
            self._nudge_rating(ratings, "pass_block_speed", length_leverage * 0.85 + mass_mobility * 0.45)
            self._nudge_rating(ratings, "reach_block", length_leverage * 0.45 + mass_mobility * 0.80)
            self._nudge_rating(ratings, "lead_block", mass_mobility * 0.55)
        elif position in {"EDGE", "OLB", "IDL"}:
            self._nudge_rating(ratings, "power_rush", anchor_power * 0.45 + mass_power * 0.35 + length_leverage * 0.30)
            self._nudge_rating(ratings, "speed_rush", mass_mobility * 0.70 + length_leverage * 0.25)
            self._nudge_rating(ratings, "block_shedding", anchor_power * 0.50 + length_leverage * 0.45)
            self._nudge_rating(ratings, "double_team_takeon", anchor_power * 1.10)
            self._nudge_rating(ratings, "gap_integrity", anchor_power * 0.55)
            self._nudge_rating(ratings, "edge_contain", length_leverage * 0.40 + mass_mobility * 0.25)
        elif position in {"CB", "NB", "FS", "SS", "ILB"}:
            self._nudge_rating(ratings, "press_coverage", length_leverage * 0.90 + mass_power * 0.25)
            self._nudge_rating(ratings, "man_coverage", mass_mobility * 0.55 + length_leverage * 0.20)
            self._nudge_rating(ratings, "zone_recovery", mass_mobility * 0.60)
            self._nudge_rating(ratings, "ball_skills", hand_catch * 0.45 + length_catch * 0.35)
            self._nudge_rating(ratings, "hit_power", mass_power * 0.75)
            self._nudge_rating(ratings, "open_field_tackle", mass_mobility * 0.35)
            self._nudge_rating(ratings, "tackle_wrap", length_leverage * 0.35 + mass_power * 0.30)

    def _skill_ratings(
        self,
        position: str,
        archetype: str,
        grade: int,
        speed: int,
        acceleration: int,
        agility: int,
        strength: int,
        balance: int,
        recognition: int,
        processing: int,
        composure: int,
        arm_delta: float,
        hand_delta: float,
    ) -> dict[str, int]:
        r = self.rng

        def primary(offset: float = 0.0, sigma: float = 4.0) -> int:
            return roll(r, grade + offset, sigma, 20, 98)

        throw_power = primary(12 + hand_delta * 1.5, 4.0)
        if archetype == "Toolsy passer":
            throw_power += 4
        pass_accuracy = primary(4 if archetype == "Rhythm passer" else 0, 4.0)

        route = primary(3 if archetype in {"Slot separator", "Possession target", "Move tight end"} else 0)
        hands = primary(4 if archetype in {"Possession target", "Mismatch target", "Receiving back"} else 1)
        run_block = primary(4 if archetype in {"Drive blocker", "Inline tight end", "Blocking specialist"} else 0)
        pass_block = primary(4 if archetype in {"Pass protector", "Anchor blocker"} else 0)
        pass_rush = primary(5 if archetype in {"Speed rusher", "Power edge", "Interior rusher"} else 0)
        coverage = primary(5 if archetype in {"Man corner", "Zone corner", "Deep safety", "Coverage linebacker", "Slot corner"} else 0)
        tackle = primary(4 if archetype in {"Box linebacker", "Box safety", "Run-setting edge", "Nose tackle"} else 0)
        power = primary(4 if archetype in {"Power back", "Power edge", "Nose tackle"} else 0)

        kick_power = primary(10 if archetype in {"Big-leg kicker", "Big-leg punter"} else 5, 4.0)
        kick_accuracy = primary(10 if archetype in {"Accurate kicker", "Field-position punter", "Directional punter"} else 5, 4.0)
        if position == "K":
            power_offset = 13 if archetype == "Big-leg kicker" else 8
            accuracy_offset = 13 if archetype in {"Accurate kicker", "Clutch kicker"} else 8
            kick_power = primary(power_offset, 5.2)
            kick_accuracy = primary(accuracy_offset, 5.0)
            if grade >= 67:
                kick_power = max(kick_power, roll(r, 80 if archetype == "Big-leg kicker" else 76, 2.5, 68, 90))
                kick_accuracy = max(kick_accuracy, roll(r, 82 if archetype in {"Accurate kicker", "Clutch kicker"} else 78, 2.5, 70, 92))
            kick_power = min(kick_power, grade + (16 if archetype == "Big-leg kicker" else 12))
            kick_accuracy = min(kick_accuracy, grade + (16 if archetype in {"Accurate kicker", "Clutch kicker"} else 12))
        elif position == "P":
            power_offset = 13 if archetype == "Big-leg punter" else 8
            accuracy_offset = 13 if archetype in {"Field-position punter", "Directional punter"} else 8
            kick_power = primary(power_offset, 5.2)
            kick_accuracy = primary(accuracy_offset, 5.0)
            if grade >= 67:
                kick_power = max(kick_power, roll(r, 83 if archetype == "Big-leg punter" else 78, 2.5, 70, 92))
                kick_accuracy = max(kick_accuracy, roll(r, 82 if archetype in {"Field-position punter", "Directional punter"} else 76, 2.5, 68, 90))
            kick_power = min(kick_power, grade + (16 if archetype == "Big-leg punter" else 12))
            kick_accuracy = min(kick_accuracy, grade + (16 if archetype in {"Field-position punter", "Directional punter"} else 12))

        return {
            "pass_accuracy_short": clamp(pass_accuracy + 3, 1, 99),
            "pass_accuracy_mid": clamp(blend((pass_accuracy, 0.78), (throw_power, 0.22)), 1, 99),
            "pass_accuracy_deep": clamp(blend((pass_accuracy, 0.55), (throw_power, 0.35), (recognition, 0.1)) - 2, 1, 99),
            "throw_power": clamp(throw_power, 1, 99),
            "throw_release": clamp(blend((pass_accuracy, 0.35), (processing, 0.25), (agility, 0.2), (grade, 0.2)), 1, 99),
            "platform_control": clamp(blend((pass_accuracy, 0.3), (agility, 0.25), (composure, 0.25), (strength, 0.2)), 1, 99),
            "carry_vision": clamp(blend((recognition, 0.45), (agility, 0.15), (grade, 0.25), (power, 0.15)), 1, 99),
            "elusiveness": clamp(blend((agility, 0.45), (speed, 0.25), (acceleration, 0.15), (grade, 0.15)), 1, 99),
            "contact_power": clamp(blend((power, 0.45), (strength, 0.35), (balance, 0.2)), 1, 99),
            "ball_security": clamp(blend((recognition, 0.3), (strength, 0.2), (balance, 0.2), (composure, 0.3)), 1, 99),
            "run_patience": clamp(blend((recognition, 0.5), (processing, 0.2), (grade, 0.3)), 1, 99),
            "release_vs_press": clamp(blend((route, 0.35), (agility, 0.25), (strength, 0.15), (hands, 0.25)), 1, 99),
            "route_snap": clamp(blend((route, 0.45), (agility, 0.35), (acceleration, 0.2)), 1, 99),
            "route_timing": clamp(blend((route, 0.5), (recognition, 0.3), (grade, 0.2)), 1, 99),
            "hands": clamp(hands + hand_delta, 1, 99),
            "contested_catch": clamp(blend((hands, 0.42), (strength, 0.18), (balance, 0.22), (composure, 0.18)) + arm_delta, 1, 99),
            "catch_in_traffic": clamp(blend((hands, 0.4), (balance, 0.25), (strength, 0.15), (composure, 0.2)), 1, 99),
            "pass_block_power": clamp(blend((pass_block, 0.55), (strength, 0.35), (balance, 0.1)), 1, 99),
            "pass_block_finesse": clamp(blend((pass_block, 0.55), (recognition, 0.25), (agility, 0.1), (grade, 0.1)), 1, 99),
            "pass_block_speed": clamp(blend((pass_block, 0.62), (recognition, 0.15), (agility, 0.13), (acceleration, 0.1)) + arm_delta, 1, 99),
            "run_block_drive": clamp(blend((run_block, 0.55), (strength, 0.35), (balance, 0.1)), 1, 99),
            "reach_block": clamp(blend((run_block, 0.43), (agility, 0.27), (acceleration, 0.2), (recognition, 0.1)), 1, 99),
            "lead_block": clamp(blend((run_block, 0.45), (speed, 0.15), (agility, 0.2), (recognition, 0.2)), 1, 99),
            "block_sustain": clamp(blend((run_block, 0.25), (pass_block, 0.25), (strength, 0.2), (balance, 0.15), (recognition, 0.15)), 1, 99),
            "power_rush": clamp(blend((pass_rush, 0.52), (strength, 0.38), (balance, 0.1)), 1, 99),
            "finesse_rush": clamp(blend((pass_rush, 0.5), (agility, 0.3), (recognition, 0.2)), 1, 99),
            "speed_rush": clamp(blend((pass_rush, 0.48), (speed, 0.2), (acceleration, 0.22), (agility, 0.1)), 1, 99),
            "rush_plan": clamp(blend((pass_rush, 0.45), (recognition, 0.35), (processing, 0.2)), 1, 99),
            "stunt_execution": clamp(blend((pass_rush, 0.4), (recognition, 0.3), (agility, 0.2), (grade, 0.1)), 1, 99),
            "double_team_takeon": clamp(blend((pass_rush, 0.25), (strength, 0.45), (balance, 0.2), (grade, 0.1)), 1, 99),
            "sack_finish": clamp(blend((pass_rush, 0.45), (tackle, 0.25), (agility, 0.15), (recognition, 0.15)), 1, 99),
            "run_diagnostics": clamp(blend((recognition, 0.55), (tackle, 0.2), (grade, 0.25)), 1, 99),
            "block_shedding": clamp(blend((pass_rush, 0.3), (tackle, 0.25), (strength, 0.3), (balance, 0.15)), 1, 99),
            "gap_integrity": clamp(blend((recognition, 0.45), (strength, 0.2), (tackle, 0.2), (grade, 0.15)), 1, 99),
            "pursuit_angle": clamp(blend((tackle, 0.3), (speed, 0.25), (agility, 0.2), (recognition, 0.25)), 1, 99),
            "edge_contain": clamp(blend((tackle, 0.25), (strength, 0.25), (speed, 0.15), (recognition, 0.35)), 1, 99),
            "traffic_navigation": clamp(blend((agility, 0.25), (strength, 0.25), (recognition, 0.3), (balance, 0.2)), 1, 99),
            "press_coverage": clamp(blend((coverage, 0.45), (strength, 0.2), (agility, 0.2), (recognition, 0.15)) + arm_delta, 1, 99),
            "man_coverage": clamp(blend((coverage, 0.5), (agility, 0.25), (speed, 0.15), (recognition, 0.1)), 1, 99),
            "zone_coverage": clamp(blend((coverage, 0.5), (recognition, 0.35), (processing, 0.15)), 1, 99),
            "zone_recovery": clamp(blend((coverage, 0.45), (speed, 0.2), (acceleration, 0.2), (agility, 0.15)), 1, 99),
            "ball_skills": clamp(blend((coverage, 0.45), (hands, 0.2), (recognition, 0.25), (composure, 0.1)), 1, 99),
            "coverage_communication": clamp(blend((coverage, 0.45), (recognition, 0.35), (grade, 0.2)), 1, 99),
            "solo_tackle": clamp(tackle, 1, 99),
            "tackle_wrap": clamp(blend((tackle, 0.45), (strength, 0.25), (balance, 0.15), (recognition, 0.15)), 1, 99),
            "hit_power": clamp(blend((tackle, 0.25), (strength, 0.4), (speed, 0.15), (balance, 0.2)), 1, 99),
            "forced_fumble": clamp(blend((tackle, 0.35), (strength, 0.25), (recognition, 0.25), (agility, 0.15)), 1, 99),
            "open_field_tackle": clamp(blend((tackle, 0.45), (agility, 0.2), (speed, 0.15), (recognition, 0.2)), 1, 99),
            "assist_tackle": clamp(blend((tackle, 0.4), (strength, 0.25), (balance, 0.2), (recognition, 0.15)), 1, 99),
            "kick_power": clamp(kick_power + (strength - 50) * 0.1, 1, 99),
            "kick_accuracy": clamp(kick_accuracy + (composure - 55) * 0.1, 1, 99),
        }

    def _fill_and_cap(self, position: str, ratings: dict[str, int]) -> dict[str, int]:
        active_groups = POSITION_ACTIVE_GROUPS.get(position, set())
        capped: dict[str, int] = {}
        for rating_key, rating_group in RATING_GROUPS.items():
            value = ratings.get(rating_key)
            if value is None:
                value = roll(self.rng, LOW_GROUP_CAPS.get(rating_group, 18), 3.0, 5, 35)
            if rating_group != "universal" and rating_group not in active_groups:
                value = min(value, roll(self.rng, LOW_GROUP_CAPS.get(rating_group, 18), 2.0, 5, 42))
            capped[rating_key] = clamp(value, 1, 99)
        return capped

    def _role_scores(
        self,
        ratings: dict[str, int],
        roles: tuple[str, ...],
        true_grade: int,
    ) -> dict[str, float]:
        scores: dict[str, float] = {}
        for role in roles:
            weights = ROLE_WEIGHTS.get(role)
            if not weights:
                continue
            available = [(ratings[key], weight) for key, weight in weights.items() if key in ratings]
            if not available:
                continue
            weighted = sum(value * weight for value, weight in available) / sum(weight for _value, weight in available)
            scores[role] = round((weighted * 0.82) + (true_grade * 0.18), 2)
        return scores

    def _risk_level(
        self,
        true_grade: int,
        ceiling_grade: int,
        dev_trait: str,
        tier: str,
        rank: int,
    ) -> str:
        boom_gap = ceiling_grade - true_grade
        risk_score = 0
        risk_score += 2 if boom_gap >= 22 else 1 if boom_gap >= 16 else 0
        risk_score += 1 if dev_trait in {"Superstar", "X-Factor"} and tier not in {"round_1", "round_2_3"} else 0
        risk_score += 1 if true_grade <= 53 else 0
        if rank <= 32 and true_grade <= 62:
            risk_score += 3
        elif rank <= 32 and true_grade <= 64:
            risk_score += 2
        elif rank <= 96 and true_grade <= 56:
            risk_score += 1
        if risk_score >= 3:
            return "High"
        if risk_score == 0 and true_grade >= 62:
            return "Low"
        return "Medium"


def rank_tier(rank: int) -> str:
    if rank <= 32:
        return "round_1"
    if rank <= 96:
        return "round_2_3"
    if rank <= 160:
        return "round_4_5"
    if rank <= 256:
        return "round_6_7"
    return "leftover"


def archetype_identity_score(archetype: str, ratings: dict[str, int]) -> float:
    traits = ARCHETYPE_CORE_TRAITS.get(archetype, ())
    values = [ratings.get(trait, 0) for trait in traits]
    return sum(values) / len(values) if values else 0.0


def archetype_failed_core_traits(
    archetype: str,
    ratings: dict[str, int],
    threshold: int,
) -> list[str]:
    failed: list[str] = []
    for trait in ARCHETYPE_CORE_TRAITS.get(archetype, ()):
        if ratings.get(trait, 0) < threshold:
            failed.append(DISPLAY_NAMES.get(trait, trait.replace("_", " ").title()))
    return failed


def rating_summary(
    ratings: dict[str, int],
    roles: tuple[str, ...],
    *,
    position: str,
    highest: bool,
) -> str:
    relevant_keys: set[str] = set()
    for role in roles:
        relevant_keys.update(ROLE_WEIGHTS.get(role, {}))
    if not relevant_keys:
        if position in {"K", "P"}:
            relevant_keys = {"kick_power", "kick_accuracy", "composure", "discipline", "durability"}
        elif position == "LS":
            relevant_keys = {
                "block_sustain",
                "pass_block_power",
                "pass_block_speed",
                "tackle_wrap",
                "durability",
                "stamina",
                "discipline",
            }
        else:
            relevant_keys = {"speed", "acceleration", "agility", "strength", "composure", "durability"}
    items = [
        (DISPLAY_NAMES.get(key, key.replace("_", " ").title()), ratings[key])
        for key in relevant_keys
        if key in ratings
    ]
    items.sort(key=lambda item: item[1], reverse=highest)
    return "; ".join(f"{name} {value}" for name, value in items[:5])


def weighted_choice(rng: random.Random, weights: dict[str, float]) -> str:
    keys = list(weights)
    values = [max(0.01, float(weights[key])) for key in keys]
    return rng.choices(keys, weights=values, k=1)[0]


def roll(rng: random.Random, mean: float, sigma: float, low: int = 1, high: int = 99) -> int:
    return clamp(rng.gauss(mean, sigma), low, high)


def blend(*weighted_values: tuple[float, float]) -> float:
    total = sum(weight for _value, weight in weighted_values)
    if total <= 0:
        return 50.0
    return sum(value * weight for value, weight in weighted_values) / total


def clamp(value: float, low: int = 1, high: int = 99) -> int:
    return max(low, min(high, int(round(value))))
