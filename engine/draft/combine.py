"""Generate NFL combine-style workout results for draft prospects.

Combine results are public-ish workout data, not the true player card. They are
usually aligned with hidden athletic ratings, but workout variance, skipped
drills, injuries, and strategic non-participation can make the numbers noisy.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from .attributes import DraftProspectAttributes, POSITION_BASELINES, blend, clamp, rank_tier


DRILL_LABELS = {
    "forty_yard_dash": "40-yard dash",
    "ten_yard_split": "10-yard split",
    "bench_press_reps": "bench press",
    "vertical_jump_in": "vertical",
    "broad_jump_in": "broad jump",
    "three_cone_sec": "3-cone",
    "twenty_yard_shuttle_sec": "20-yard shuttle",
    "sixty_yard_shuttle_sec": "60-yard shuttle",
}

DRILLS = tuple(DRILL_LABELS)

POSITION_DRILL_CHANCES: dict[str, dict[str, float]] = {
    "QB": {
        "forty_yard_dash": 0.78,
        "ten_yard_split": 0.70,
        "bench_press_reps": 0.06,
        "vertical_jump_in": 0.65,
        "broad_jump_in": 0.68,
        "three_cone_sec": 0.45,
        "twenty_yard_shuttle_sec": 0.45,
        "sixty_yard_shuttle_sec": 0.12,
    },
    "RB": {
        "forty_yard_dash": 0.88,
        "ten_yard_split": 0.82,
        "bench_press_reps": 0.62,
        "vertical_jump_in": 0.82,
        "broad_jump_in": 0.82,
        "three_cone_sec": 0.54,
        "twenty_yard_shuttle_sec": 0.58,
        "sixty_yard_shuttle_sec": 0.18,
    },
    "FB": {
        "forty_yard_dash": 0.82,
        "ten_yard_split": 0.76,
        "bench_press_reps": 0.70,
        "vertical_jump_in": 0.70,
        "broad_jump_in": 0.72,
        "three_cone_sec": 0.48,
        "twenty_yard_shuttle_sec": 0.54,
        "sixty_yard_shuttle_sec": 0.16,
    },
    "WR": {
        "forty_yard_dash": 0.90,
        "ten_yard_split": 0.84,
        "bench_press_reps": 0.40,
        "vertical_jump_in": 0.84,
        "broad_jump_in": 0.86,
        "three_cone_sec": 0.50,
        "twenty_yard_shuttle_sec": 0.54,
        "sixty_yard_shuttle_sec": 0.16,
    },
    "TE": {
        "forty_yard_dash": 0.86,
        "ten_yard_split": 0.80,
        "bench_press_reps": 0.62,
        "vertical_jump_in": 0.78,
        "broad_jump_in": 0.78,
        "three_cone_sec": 0.48,
        "twenty_yard_shuttle_sec": 0.52,
        "sixty_yard_shuttle_sec": 0.14,
    },
    "OT": {
        "forty_yard_dash": 0.82,
        "ten_yard_split": 0.80,
        "bench_press_reps": 0.72,
        "vertical_jump_in": 0.72,
        "broad_jump_in": 0.72,
        "three_cone_sec": 0.44,
        "twenty_yard_shuttle_sec": 0.46,
        "sixty_yard_shuttle_sec": 0.10,
    },
    "OG": {
        "forty_yard_dash": 0.80,
        "ten_yard_split": 0.78,
        "bench_press_reps": 0.74,
        "vertical_jump_in": 0.70,
        "broad_jump_in": 0.70,
        "three_cone_sec": 0.40,
        "twenty_yard_shuttle_sec": 0.44,
        "sixty_yard_shuttle_sec": 0.10,
    },
    "C": {
        "forty_yard_dash": 0.80,
        "ten_yard_split": 0.78,
        "bench_press_reps": 0.74,
        "vertical_jump_in": 0.70,
        "broad_jump_in": 0.70,
        "three_cone_sec": 0.42,
        "twenty_yard_shuttle_sec": 0.46,
        "sixty_yard_shuttle_sec": 0.10,
    },
    "IDL": {
        "forty_yard_dash": 0.82,
        "ten_yard_split": 0.82,
        "bench_press_reps": 0.76,
        "vertical_jump_in": 0.74,
        "broad_jump_in": 0.74,
        "three_cone_sec": 0.42,
        "twenty_yard_shuttle_sec": 0.44,
        "sixty_yard_shuttle_sec": 0.10,
    },
    "EDGE": {
        "forty_yard_dash": 0.86,
        "ten_yard_split": 0.84,
        "bench_press_reps": 0.70,
        "vertical_jump_in": 0.78,
        "broad_jump_in": 0.80,
        "three_cone_sec": 0.48,
        "twenty_yard_shuttle_sec": 0.50,
        "sixty_yard_shuttle_sec": 0.12,
    },
    "ILB": {
        "forty_yard_dash": 0.86,
        "ten_yard_split": 0.80,
        "bench_press_reps": 0.66,
        "vertical_jump_in": 0.78,
        "broad_jump_in": 0.78,
        "three_cone_sec": 0.48,
        "twenty_yard_shuttle_sec": 0.52,
        "sixty_yard_shuttle_sec": 0.14,
    },
    "CB": {
        "forty_yard_dash": 0.90,
        "ten_yard_split": 0.84,
        "bench_press_reps": 0.34,
        "vertical_jump_in": 0.84,
        "broad_jump_in": 0.84,
        "three_cone_sec": 0.52,
        "twenty_yard_shuttle_sec": 0.56,
        "sixty_yard_shuttle_sec": 0.16,
    },
    "NB": {
        "forty_yard_dash": 0.90,
        "ten_yard_split": 0.84,
        "bench_press_reps": 0.34,
        "vertical_jump_in": 0.84,
        "broad_jump_in": 0.84,
        "three_cone_sec": 0.52,
        "twenty_yard_shuttle_sec": 0.56,
        "sixty_yard_shuttle_sec": 0.16,
    },
    "FS": {
        "forty_yard_dash": 0.88,
        "ten_yard_split": 0.82,
        "bench_press_reps": 0.44,
        "vertical_jump_in": 0.80,
        "broad_jump_in": 0.80,
        "three_cone_sec": 0.50,
        "twenty_yard_shuttle_sec": 0.54,
        "sixty_yard_shuttle_sec": 0.16,
    },
    "SS": {
        "forty_yard_dash": 0.88,
        "ten_yard_split": 0.82,
        "bench_press_reps": 0.48,
        "vertical_jump_in": 0.80,
        "broad_jump_in": 0.80,
        "three_cone_sec": 0.50,
        "twenty_yard_shuttle_sec": 0.54,
        "sixty_yard_shuttle_sec": 0.16,
    },
    "K": {
        "forty_yard_dash": 0.28,
        "ten_yard_split": 0.20,
        "bench_press_reps": 0.04,
        "vertical_jump_in": 0.18,
        "broad_jump_in": 0.18,
        "three_cone_sec": 0.08,
        "twenty_yard_shuttle_sec": 0.10,
        "sixty_yard_shuttle_sec": 0.02,
    },
    "P": {
        "forty_yard_dash": 0.28,
        "ten_yard_split": 0.20,
        "bench_press_reps": 0.04,
        "vertical_jump_in": 0.18,
        "broad_jump_in": 0.18,
        "three_cone_sec": 0.08,
        "twenty_yard_shuttle_sec": 0.10,
        "sixty_yard_shuttle_sec": 0.02,
    },
    "LS": {
        "forty_yard_dash": 0.58,
        "ten_yard_split": 0.52,
        "bench_press_reps": 0.60,
        "vertical_jump_in": 0.42,
        "broad_jump_in": 0.42,
        "three_cone_sec": 0.26,
        "twenty_yard_shuttle_sec": 0.32,
        "sixty_yard_shuttle_sec": 0.08,
    },
}

COMBINE_BASELINES: dict[str, dict[str, float]] = {
    "QB": {"forty": 4.82, "ten": 1.66, "bench": 0, "vertical": 31.0, "broad": 112, "cone": 7.15, "shuttle": 4.35, "sixty": 11.70},
    "RB": {"forty": 4.52, "ten": 1.56, "bench": 20, "vertical": 35.5, "broad": 121, "cone": 7.05, "shuttle": 4.25, "sixty": 11.30},
    "FB": {"forty": 4.78, "ten": 1.64, "bench": 23, "vertical": 32.0, "broad": 114, "cone": 7.25, "shuttle": 4.40, "sixty": 11.75},
    "WR": {"forty": 4.49, "ten": 1.55, "bench": 15, "vertical": 36.0, "broad": 123, "cone": 6.95, "shuttle": 4.23, "sixty": 11.25},
    "TE": {"forty": 4.73, "ten": 1.63, "bench": 21, "vertical": 33.0, "broad": 116, "cone": 7.20, "shuttle": 4.38, "sixty": 11.65},
    "OT": {"forty": 5.18, "ten": 1.78, "bench": 25, "vertical": 28.5, "broad": 104, "cone": 7.75, "shuttle": 4.78, "sixty": 12.45},
    "OG": {"forty": 5.24, "ten": 1.80, "bench": 26, "vertical": 28.0, "broad": 103, "cone": 7.85, "shuttle": 4.82, "sixty": 12.55},
    "C": {"forty": 5.22, "ten": 1.79, "bench": 25, "vertical": 28.5, "broad": 103, "cone": 7.80, "shuttle": 4.80, "sixty": 12.50},
    "IDL": {"forty": 5.05, "ten": 1.74, "bench": 27, "vertical": 29.5, "broad": 108, "cone": 7.55, "shuttle": 4.68, "sixty": 12.25},
    "EDGE": {"forty": 4.75, "ten": 1.63, "bench": 24, "vertical": 33.5, "broad": 118, "cone": 7.20, "shuttle": 4.42, "sixty": 11.80},
    "ILB": {"forty": 4.67, "ten": 1.61, "bench": 22, "vertical": 33.0, "broad": 116, "cone": 7.15, "shuttle": 4.35, "sixty": 11.70},
    "CB": {"forty": 4.46, "ten": 1.54, "bench": 15, "vertical": 36.0, "broad": 123, "cone": 6.90, "shuttle": 4.15, "sixty": 11.15},
    "NB": {"forty": 4.45, "ten": 1.54, "bench": 15, "vertical": 36.0, "broad": 123, "cone": 6.88, "shuttle": 4.14, "sixty": 11.12},
    "FS": {"forty": 4.55, "ten": 1.57, "bench": 17, "vertical": 34.5, "broad": 119, "cone": 7.00, "shuttle": 4.22, "sixty": 11.35},
    "SS": {"forty": 4.58, "ten": 1.58, "bench": 18, "vertical": 34.0, "broad": 118, "cone": 7.05, "shuttle": 4.25, "sixty": 11.42},
    "K": {"forty": 4.95, "ten": 1.70, "bench": 0, "vertical": 29.0, "broad": 107, "cone": 7.35, "shuttle": 4.45, "sixty": 11.90},
    "P": {"forty": 5.02, "ten": 1.72, "bench": 0, "vertical": 29.0, "broad": 107, "cone": 7.35, "shuttle": 4.45, "sixty": 11.90},
    "LS": {"forty": 5.02, "ten": 1.72, "bench": 21, "vertical": 29.5, "broad": 108, "cone": 7.45, "shuttle": 4.55, "sixty": 12.05},
}


@dataclass(frozen=True)
class CombineProfile:
    status: str
    participation_note: str
    combine_grade: int | None
    athletic_score: int | None
    drills_completed: int
    drills_skipped: str
    is_injured: bool
    is_top_skip: bool
    workout_variance: str
    forty_yard_dash: float | None
    ten_yard_split: float | None
    bench_press_reps: int | None
    vertical_jump_in: float | None
    broad_jump_in: int | None
    three_cone_sec: float | None
    twenty_yard_shuttle_sec: float | None
    sixty_yard_shuttle_sec: float | None

    @property
    def summary(self) -> str:
        if self.drills_completed == 0:
            return self.participation_note
        bits = []
        if self.forty_yard_dash is not None:
            bits.append(f"40 {self.forty_yard_dash:.2f}")
        if self.bench_press_reps is not None:
            bits.append(f"bench {self.bench_press_reps}")
        if self.vertical_jump_in is not None:
            bits.append(f"vert {self.vertical_jump_in:.1f}")
        if self.broad_jump_in is not None:
            bits.append(f"broad {self.broad_jump_in}")
        if self.three_cone_sec is not None:
            bits.append(f"3-cone {self.three_cone_sec:.2f}")
        if self.twenty_yard_shuttle_sec is not None:
            bits.append(f"shuttle {self.twenty_yard_shuttle_sec:.2f}")
        return "; ".join(bits)


class CombineGenerator:
    """Generate realistic but noisy NFL combine participation and drill results."""

    def __init__(self, *, seed: str | int | None = None) -> None:
        self.seed = seed
        self.rng = random.Random(seed)

    def generate(
        self,
        *,
        position: str,
        rank: int,
        position_rank: int,
        height_in: int,
        weight_lbs: int,
        attributes: DraftProspectAttributes,
        invitation_profile: str = "public_board",
    ) -> CombineProfile:
        position = position.upper()
        if invitation_profile == "hidden_unlisted" and not self._hidden_combine_invited(attributes):
            return CombineProfile(
                status="Not invited",
                participation_note=self.rng.choice(
                    [
                        "Did not receive a national combine invite; evaluation starts from pro day, tape, and regional scout notes.",
                        "Outside the initial combine list, leaving teams to build the profile through area scouting.",
                        "No combine invitation logged; testing exposure will depend on later school or regional workouts.",
                    ]
                ),
                combine_grade=None,
                athletic_score=None,
                drills_completed=0,
                drills_skipped=", ".join(DRILL_LABELS.values()),
                is_injured=False,
                is_top_skip=False,
                workout_variance="No combine invite",
                forty_yard_dash=None,
                ten_yard_split=None,
                bench_press_reps=None,
                vertical_jump_in=None,
                broad_jump_in=None,
                three_cone_sec=None,
                twenty_yard_shuttle_sec=None,
                sixty_yard_shuttle_sec=None,
            )
        participation = self._participation(
            position=position,
            rank=rank,
            position_rank=position_rank,
            durability=attributes.ratings.get("durability", 60),
        )
        if participation["full_skip"]:
            return CombineProfile(
                status=participation["status"],
                participation_note=participation["note"],
                combine_grade=None,
                athletic_score=None,
                drills_completed=0,
                drills_skipped=", ".join(DRILL_LABELS.values()),
                is_injured=participation["is_injured"],
                is_top_skip=participation["is_top_skip"],
                workout_variance="Unavailable",
                forty_yard_dash=None,
                ten_yard_split=None,
                bench_press_reps=None,
                vertical_jump_in=None,
                broad_jump_in=None,
                three_cone_sec=None,
                twenty_yard_shuttle_sec=None,
                sixty_yard_shuttle_sec=None,
            )

        workout = self._workout_day()
        drills = self._drill_participation(position, rank, participation)
        results = self._results(
            position=position,
            height_in=height_in,
            weight_lbs=weight_lbs,
            attributes=attributes,
            drills=drills,
            workout_modifier=workout["modifier"],
            limited=participation["is_limited"],
        )
        drills_completed = sum(value is not None for value in results.values())
        skipped = [
            DRILL_LABELS[key]
            for key in DRILLS
            if results[key] is None
        ]
        athletic_score = self._athletic_score(position, attributes.ratings)
        combine_grade = self._combine_grade(athletic_score, results, workout["modifier"], participation["is_limited"])
        status = "Limited participant" if participation["is_limited"] else "Full participant"
        note = str(participation["note"])
        if drills_completed <= 3:
            status = "Partial participant"
            if not participation["is_limited"]:
                note = self.rng.choice(
                    [
                        "Completed a selective workout and skipped several optional drills.",
                        "Worked out in only a limited set of timed and measured drills.",
                        "Posted partial testing, with the rest likely saved for pro day.",
                    ]
                )
        return CombineProfile(
            status=status,
            participation_note=note,
            combine_grade=combine_grade,
            athletic_score=athletic_score,
            drills_completed=drills_completed,
            drills_skipped=", ".join(skipped),
            is_injured=participation["is_injured"],
            is_top_skip=participation["is_top_skip"],
            workout_variance=workout["label"],
            forty_yard_dash=results["forty_yard_dash"],
            ten_yard_split=results["ten_yard_split"],
            bench_press_reps=results["bench_press_reps"],
            vertical_jump_in=results["vertical_jump_in"],
            broad_jump_in=results["broad_jump_in"],
            three_cone_sec=results["three_cone_sec"],
            twenty_yard_shuttle_sec=results["twenty_yard_shuttle_sec"],
            sixty_yard_shuttle_sec=results["sixty_yard_shuttle_sec"],
        )

    def _hidden_combine_invited(self, attributes: DraftProspectAttributes) -> bool:
        true_grade = int(attributes.true_grade)
        ceiling = int(attributes.ceiling_grade)
        athletic_core = [
            attributes.ratings.get("speed"),
            attributes.ratings.get("acceleration"),
            attributes.ratings.get("agility"),
            attributes.ratings.get("strength"),
        ]
        athletic_values = [value for value in athletic_core if value is not None]
        athletic_avg = sum(athletic_values) / len(athletic_values) if athletic_values else 55.0
        chance = (
            0.12
            + max(-0.04, min(0.20, (true_grade - 55) * 0.018))
            + max(-0.03, min(0.10, (ceiling - 64) * 0.008))
            + max(-0.03, min(0.08, (athletic_avg - 62) * 0.004))
            + self.rng.gauss(0.0, 0.035)
        )
        chance = max(0.04, min(0.42, chance))
        return self.rng.random() < chance

    def _participation(
        self,
        *,
        position: str,
        rank: int,
        position_rank: int,
        durability: int,
    ) -> dict[str, object]:
        tier = rank_tier(rank)
        injured_dnp = {
            "round_1": 0.035,
            "round_2_3": 0.045,
            "round_4_5": 0.055,
            "round_6_7": 0.060,
            "leftover": 0.050,
        }[tier]
        injured_dnp += max(0, 62 - durability) * 0.002
        top_skip = {
            "round_1": 0.10,
            "round_2_3": 0.035,
            "round_4_5": 0.012,
            "round_6_7": 0.006,
            "leftover": 0.003,
        }[tier]
        if position_rank == 1 and rank <= 48:
            top_skip += 0.10
        elif position_rank <= 3 and rank <= 32 and position in {"QB", "WR", "CB", "EDGE", "OT"}:
            top_skip += 0.05
        if position in {"K", "P"}:
            top_skip += 0.08

        roll_value = self.rng.random()
        if roll_value < injured_dnp:
            return {
                "full_skip": True,
                "is_injured": True,
                "is_top_skip": False,
                "is_limited": True,
                "status": "Injured - did not participate",
                "note": self.rng.choice(
                    [
                        "Medical flag kept him out of combine drills.",
                        "Could not participate because of a pre-draft injury.",
                        "Held out of athletic testing after medical checks.",
                    ]
                ),
            }
        if roll_value < injured_dnp + top_skip:
            return {
                "full_skip": True,
                "is_injured": False,
                "is_top_skip": True,
                "is_limited": False,
                "status": "Did not participate",
                "note": self.rng.choice(
                    [
                        "Chose to wait for pro day testing.",
                        "Skipped workout drills after team meetings and measurements.",
                        "Agent-managed workout plan kept him out of combine testing.",
                    ]
                ),
            }

        limited_chance = 0.06 + max(0, 58 - durability) * 0.002
        if rank <= 32:
            limited_chance += 0.035
        if self.rng.random() < limited_chance:
            return {
                "full_skip": False,
                "is_injured": self.rng.random() < 0.45,
                "is_top_skip": False,
                "is_limited": True,
                "status": "Limited participant",
                "note": self.rng.choice(
                    [
                        "Limited workout; skipped at least one key drill.",
                        "Medical precaution shaped a partial workout.",
                        "Selected drills only, with the rest likely saved for pro day.",
                    ]
                ),
            }
        return {
            "full_skip": False,
            "is_injured": False,
            "is_top_skip": False,
            "is_limited": False,
            "status": "Full participant",
            "note": "Completed a normal combine workout for his position group.",
        }

    def _workout_day(self) -> dict[str, float | str]:
        roll_value = self.rng.random()
        if roll_value < 0.055:
            return {"modifier": self.rng.uniform(4.0, 8.0), "label": "Hot workout"}
        if roll_value < 0.12:
            return {"modifier": -self.rng.uniform(4.0, 8.0), "label": "Cold workout"}
        return {"modifier": self.rng.gauss(0, 2.0), "label": "Normal variance"}

    def _drill_participation(
        self,
        position: str,
        rank: int,
        participation: dict[str, object],
    ) -> dict[str, bool]:
        chances = dict(POSITION_DRILL_CHANCES.get(position, POSITION_DRILL_CHANCES["WR"]))
        if rank <= 32:
            chances["bench_press_reps"] *= 0.82
            chances["three_cone_sec"] *= 0.82
            chances["twenty_yard_shuttle_sec"] *= 0.86
        if participation["is_limited"]:
            for key in chances:
                chances[key] *= 0.58
            for key in ("forty_yard_dash", "three_cone_sec", "twenty_yard_shuttle_sec", "sixty_yard_shuttle_sec"):
                chances[key] *= 0.72
        if position == "QB" and rank <= 64:
            chances["forty_yard_dash"] *= 0.88
            chances["three_cone_sec"] *= 0.72
            chances["twenty_yard_shuttle_sec"] *= 0.76
        return {
            key: self.rng.random() < chance
            for key, chance in chances.items()
        }

    def _workout_noise(self, sigma: float, workout_modifier: float, *, limit: float) -> float:
        noise = self.rng.gauss(0, sigma)
        if abs(workout_modifier) >= 4.0 and self.rng.random() < 0.35:
            noise += self.rng.choice((-1, 1)) * self.rng.uniform(sigma * 0.65, sigma * 1.45)
        return _clamp_float(noise, -limit, limit)

    def _results(
        self,
        *,
        position: str,
        height_in: int,
        weight_lbs: int,
        attributes: DraftProspectAttributes,
        drills: dict[str, bool],
        workout_modifier: float,
        limited: bool,
    ) -> dict[str, float | int | None]:
        ratings = attributes.ratings
        baseline = COMBINE_BASELINES.get(position, COMBINE_BASELINES["WR"])
        position_baseline = POSITION_BASELINES.get(position, POSITION_BASELINES["WR"])
        speed = ratings.get("speed", 50)
        acceleration = ratings.get("acceleration", speed)
        agility = ratings.get("agility", 50)
        strength = ratings.get("strength", 50)
        balance = ratings.get("balance", 50)
        weight_delta = weight_lbs - position_baseline["weight"]
        height_delta = height_in - position_baseline["height"]
        frame_density_delta = weight_delta - height_delta * 7.0
        workout_penalty = -workout_modifier if limited and workout_modifier > 0 else workout_modifier

        speed_delta = speed - position_baseline["speed"]
        acceleration_delta = acceleration - position_baseline["speed"]
        agility_delta = agility - position_baseline["agility"]
        strength_delta = strength - position_baseline["strength"]
        explosion = blend((speed_delta, 0.28), (acceleration_delta, 0.36), (agility_delta, 0.18), (strength_delta, 0.18))

        forty = None
        if drills.get("forty_yard_dash"):
            forty = round(
                _clamp_float(
                    baseline["forty"]
                    - speed_delta * 0.010
                    - acceleration_delta * 0.004
                    + max(0, weight_delta) * 0.0012
                    + self._workout_noise(0.055, workout_modifier, limit=0.14)
                    - workout_penalty * 0.010,
                    4.20,
                    5.65,
                ),
                2,
            )

        ten = None
        if drills.get("ten_yard_split"):
            ten_base = baseline["ten"]
            if forty is not None:
                ten_base += (forty - baseline["forty"]) * 0.32
            ten = round(
                _clamp_float(
                    ten_base
                    - acceleration_delta * 0.0035
                    - agility_delta * 0.001
                    + self._workout_noise(0.025, workout_modifier, limit=0.065)
                    - workout_penalty * 0.003,
                    1.43,
                    1.98,
                ),
                2,
            )

        bench = None
        if drills.get("bench_press_reps") and baseline["bench"] > 0:
            bench = clamp(
                baseline["bench"]
                + strength_delta * 0.42
                + frame_density_delta * 0.045
                + max(0, height_delta) * -0.20
                + self._workout_noise(3.4, workout_modifier, limit=8.0)
                + workout_penalty * 0.35,
                4,
                45,
            )

        vertical = None
        if drills.get("vertical_jump_in"):
            vertical = round(
                _clamp_float(
                    baseline["vertical"]
                    + explosion * 0.26
                    - max(0, weight_delta) * 0.018
                    + self._workout_noise(2.1, workout_modifier, limit=5.5)
                    + workout_penalty * 0.28,
                    18.0,
                    46.5,
                )
                * 2
                / 2,
                1,
            )

        broad = None
        if drills.get("broad_jump_in"):
            broad = clamp(
                baseline["broad"]
                + explosion * 0.86
                - max(0, weight_delta) * 0.035
                + self._workout_noise(5.0, workout_modifier, limit=13.0)
                + workout_penalty * 0.90,
                84,
                140,
            )

        cone = None
        if drills.get("three_cone_sec"):
            cone = round(
                _clamp_float(
                    baseline["cone"]
                    - agility_delta * 0.012
                    - acceleration_delta * 0.002
                    - balance * 0.0013
                    + max(0, weight_delta) * 0.0012
                    + self._workout_noise(0.10, workout_modifier, limit=0.26)
                    - workout_penalty * 0.010,
                    6.45,
                    8.65,
                ),
                2,
            )

        shuttle = None
        if drills.get("twenty_yard_shuttle_sec"):
            shuttle = round(
                _clamp_float(
                    baseline["shuttle"]
                    - agility_delta * 0.008
                    - acceleration_delta * 0.004
                    + max(0, weight_delta) * 0.0010
                    + self._workout_noise(0.08, workout_modifier, limit=0.20)
                    - workout_penalty * 0.008,
                    3.82,
                    5.35,
                ),
                2,
            )

        sixty = None
        if drills.get("sixty_yard_shuttle_sec"):
            sixty = round(
                _clamp_float(
                    baseline["sixty"]
                    - agility_delta * 0.012
                    - acceleration_delta * 0.006
                    + max(0, weight_delta) * 0.0015
                    + self._workout_noise(0.18, workout_modifier, limit=0.42)
                    - workout_penalty * 0.015,
                    10.45,
                    13.80,
                ),
                2,
            )

        return {
            "forty_yard_dash": forty,
            "ten_yard_split": ten,
            "bench_press_reps": bench,
            "vertical_jump_in": vertical,
            "broad_jump_in": broad,
            "three_cone_sec": cone,
            "twenty_yard_shuttle_sec": shuttle,
            "sixty_yard_shuttle_sec": sixty,
        }

    def _athletic_score(self, position: str, ratings: dict[str, int]) -> int:
        if position in {"OT", "OG", "C", "IDL"}:
            return clamp(
                blend(
                    (ratings.get("strength", 50), 0.34),
                    (ratings.get("acceleration", 50), 0.18),
                    (ratings.get("agility", 50), 0.16),
                    (ratings.get("balance", 50), 0.16),
                    (ratings.get("speed", 50), 0.10),
                    (ratings.get("durability", 50), 0.06),
                ),
                1,
                99,
            )
        if position in {"K", "P"}:
            return clamp(
                blend(
                    (ratings.get("kick_power", 50), 0.42),
                    (ratings.get("kick_accuracy", 50), 0.26),
                    (ratings.get("composure", 50), 0.16),
                    (ratings.get("strength", 50), 0.10),
                    (ratings.get("durability", 50), 0.06),
                ),
                1,
                99,
            )
        return clamp(
            blend(
                (ratings.get("speed", 50), 0.30),
                (ratings.get("acceleration", 50), 0.24),
                (ratings.get("agility", 50), 0.20),
                (ratings.get("strength", 50), 0.14),
                (ratings.get("balance", 50), 0.08),
                (ratings.get("durability", 50), 0.04),
            ),
            1,
            99,
        )

    def _combine_grade(
        self,
        athletic_score: int,
        results: dict[str, float | int | None],
        workout_modifier: float,
        limited: bool,
    ) -> int | None:
        completed = sum(value is not None for value in results.values())
        if completed == 0:
            return None
        completion_bonus = min(4, completed - 3)
        limited_penalty = 2 if limited else 0
        return clamp(
            athletic_score
            + workout_modifier * 0.75
            + completion_bonus
            - limited_penalty
            + self.rng.gauss(0, 2.0),
            25,
            99,
        )


def _clamp_float(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))
