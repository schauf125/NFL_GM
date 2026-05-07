"""Generate pro-day and private-workout data for draft prospects.

These events sit between raw combine testing and future team-specific scouting.
Pro days are public-ish workout results with their own noise. Private workouts
are hidden scouting hooks for later interviews, medical checks, and AI GM logic.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from .attributes import DraftProspectAttributes, clamp, rank_tier
from .combine import DRILLS, DRILL_LABELS, POSITION_DRILL_CHANCES, CombineGenerator, CombineProfile


@dataclass(frozen=True)
class ProDayProfile:
    status: str
    participation_note: str
    pro_day_grade: int | None
    athletic_score: int | None
    drills_completed: int
    drills_skipped: str
    workout_variance: str
    improved_from_combine: bool
    medical_recheck: bool
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


@dataclass(frozen=True)
class PrivateWorkoutProfile:
    status: str
    workout_type: str
    interest_level: str
    outcome_grade: int | None
    note: str
    hidden: bool = True


class ProDayGenerator:
    """Generate pro-day testing as a second, noisier public workout sample."""

    def __init__(self, *, seed: str | int | None = None) -> None:
        self.seed = seed
        self.rng = random.Random(seed)
        self.combine_like = CombineGenerator(seed=f"{seed}:combine-like")

    def generate(
        self,
        *,
        position: str,
        rank: int,
        height_in: int,
        weight_lbs: int,
        attributes: DraftProspectAttributes,
        combine: CombineProfile,
        college_tier: str,
    ) -> ProDayProfile:
        position = position.upper()
        chance = self._participation_chance(rank, combine, college_tier)
        if self.rng.random() > chance:
            return self._empty_profile("No pro day data", "No meaningful pro-day workout data logged.")

        if combine.is_injured and self.rng.random() < 0.32:
            return self._empty_profile(
                "Medical recheck only",
                "Returned for medical checks but did not complete athletic testing.",
                medical_recheck=True,
            )

        workout = self._workout_day(combine)
        drills = self._drill_participation(position, rank, combine)
        results = self.combine_like._results(
            position=position,
            height_in=height_in,
            weight_lbs=weight_lbs,
            attributes=attributes,
            drills=drills,
            workout_modifier=float(workout["modifier"]),
            limited=False,
        )
        completed = sum(value is not None for value in results.values())
        if completed == 0:
            return self._empty_profile(
                "Position drills only",
                "Worked through position drills but did not record timed or measured results.",
            )

        athletic_score = self.combine_like._athletic_score(position, attributes.ratings)
        pro_day_grade = self.combine_like._combine_grade(
            athletic_score,
            results,
            float(workout["modifier"]),
            False,
        )
        if pro_day_grade is not None:
            pro_day_grade = clamp(pro_day_grade + self.rng.gauss(1.5, 1.5), 25, 99)
        skipped = [
            DRILL_LABELS[key]
            for key in DRILLS
            if results[key] is None
        ]
        improved = (
            combine.combine_grade is not None
            and pro_day_grade is not None
            and pro_day_grade >= combine.combine_grade + 3
        )
        return ProDayProfile(
            status="Completed pro day" if completed >= 3 else "Limited pro day",
            participation_note=self._note(combine, improved),
            pro_day_grade=pro_day_grade,
            athletic_score=athletic_score,
            drills_completed=completed,
            drills_skipped=", ".join(skipped),
            workout_variance=str(workout["label"]),
            improved_from_combine=improved,
            medical_recheck=False,
            forty_yard_dash=results["forty_yard_dash"],
            ten_yard_split=results["ten_yard_split"],
            bench_press_reps=results["bench_press_reps"],
            vertical_jump_in=results["vertical_jump_in"],
            broad_jump_in=results["broad_jump_in"],
            three_cone_sec=results["three_cone_sec"],
            twenty_yard_shuttle_sec=results["twenty_yard_shuttle_sec"],
            sixty_yard_shuttle_sec=results["sixty_yard_shuttle_sec"],
        )

    def _participation_chance(
        self,
        rank: int,
        combine: CombineProfile,
        college_tier: str,
    ) -> float:
        tier = rank_tier(rank)
        chance = {
            "round_1": 0.52,
            "round_2_3": 0.46,
            "round_4_5": 0.40,
            "round_6_7": 0.34,
            "leftover": 0.28,
        }[tier]
        if combine.drills_completed == 0:
            chance += 0.28
        elif combine.drills_completed <= 3:
            chance += 0.18
        elif combine.combine_grade is not None and combine.combine_grade < 58:
            chance += 0.10
        else:
            chance -= 0.10
        if combine.is_top_skip:
            chance += 0.22
        if combine.is_injured:
            chance -= 0.08
        if college_tier in {"Small", "International"}:
            chance += 0.08
        return max(0.08, min(0.84, chance))

    def _drill_participation(
        self,
        position: str,
        rank: int,
        combine: CombineProfile,
    ) -> dict[str, bool]:
        chances = dict(POSITION_DRILL_CHANCES.get(position, POSITION_DRILL_CHANCES["WR"]))
        if combine.drills_completed == 0:
            multiplier = 1.16
        elif combine.drills_completed <= 3:
            multiplier = 1.02
        else:
            multiplier = 0.58
        if combine.is_top_skip:
            multiplier += 0.18
        if rank <= 32:
            chances["bench_press_reps"] *= 0.70
            chances["three_cone_sec"] *= 0.80
            chances["twenty_yard_shuttle_sec"] *= 0.82
        return {key: self.rng.random() < min(0.95, chance * multiplier) for key, chance in chances.items()}

    def _workout_day(self, combine: CombineProfile) -> dict[str, float | str]:
        roll_value = self.rng.random()
        if roll_value < 0.10:
            return {"modifier": self.rng.uniform(4.0, 8.5), "label": "Fast pro day"}
        if roll_value < 0.16:
            return {"modifier": -self.rng.uniform(3.0, 7.0), "label": "Disappointing pro day"}
        modifier = self.rng.gauss(1.2, 2.4)
        if combine.combine_grade is not None and combine.combine_grade < 55:
            modifier += self.rng.uniform(-1.0, 4.0)
        return {"modifier": modifier, "label": "Normal pro-day variance"}

    def _note(self, combine: CombineProfile, improved: bool) -> str:
        if combine.drills_completed == 0:
            return "Filled in workout data after limited or missing combine testing."
        if improved:
            return "Improved on combine testing in a more controlled pro-day setting."
        return self.rng.choice(
            [
                "Added a second workout sample after the combine.",
                "Confirmed most of the combine athletic profile.",
                "Posted supplemental testing with normal pro-day context.",
            ]
        )

    @staticmethod
    def _empty_profile(
        status: str,
        note: str,
        *,
        medical_recheck: bool = False,
    ) -> ProDayProfile:
        return ProDayProfile(
            status=status,
            participation_note=note,
            pro_day_grade=None,
            athletic_score=None,
            drills_completed=0,
            drills_skipped=", ".join(DRILL_LABELS.values()),
            workout_variance="Unavailable",
            improved_from_combine=False,
            medical_recheck=medical_recheck,
            forty_yard_dash=None,
            ten_yard_split=None,
            bench_press_reps=None,
            vertical_jump_in=None,
            broad_jump_in=None,
            three_cone_sec=None,
            twenty_yard_shuttle_sec=None,
            sixty_yard_shuttle_sec=None,
        )


class PrivateWorkoutGenerator:
    """Generate hidden private-workout/interview hooks for prospects."""

    def __init__(self, *, seed: str | int | None = None) -> None:
        self.seed = seed
        self.rng = random.Random(seed)

    def generate(
        self,
        *,
        position: str,
        rank: int,
        college_tier: str,
        attributes: DraftProspectAttributes,
        combine: CombineProfile,
        pro_day: ProDayProfile,
    ) -> PrivateWorkoutProfile:
        chance = self._workout_chance(position, rank, college_tier, attributes, combine, pro_day)
        if self.rng.random() > chance:
            return PrivateWorkoutProfile(
                status="None logged",
                workout_type="None",
                interest_level="Normal",
                outcome_grade=None,
                note="No generated private workout or visit hook.",
            )

        workout_type = self._workout_type(position, combine, pro_day)
        outcome_grade = clamp(
            attributes.true_grade
            + self.rng.gauss(0, 5.0)
            + (2 if workout_type in {"Top-30 visit", "Private throwing session"} else 0),
            25,
            95,
        )
        interest = self._interest_level(rank, position, outcome_grade, attributes.risk_level)
        note = self._note(workout_type, outcome_grade, attributes.true_grade)
        return PrivateWorkoutProfile(
            status="Logged",
            workout_type=workout_type,
            interest_level=interest,
            outcome_grade=outcome_grade,
            note=note,
        )

    def _workout_chance(
        self,
        position: str,
        rank: int,
        college_tier: str,
        attributes: DraftProspectAttributes,
        combine: CombineProfile,
        pro_day: ProDayProfile,
    ) -> float:
        chance = {
            "round_1": 0.60,
            "round_2_3": 0.46,
            "round_4_5": 0.32,
            "round_6_7": 0.22,
            "leftover": 0.12,
        }[rank_tier(rank)]
        if position.upper() == "QB":
            chance += 0.18
        if college_tier in {"Small", "International"}:
            chance += 0.08
        if combine.is_injured or pro_day.medical_recheck or attributes.risk_level == "High":
            chance += 0.08
        if attributes.ceiling_grade - attributes.true_grade >= 13:
            chance += 0.05
        return max(0.04, min(0.78, chance))

    def _workout_type(
        self,
        position: str,
        combine: CombineProfile,
        pro_day: ProDayProfile,
    ) -> str:
        if combine.is_injured or pro_day.medical_recheck:
            return "Medical recheck"
        if position.upper() == "QB":
            return self.rng.choice(["Private throwing session", "Top-30 visit", "Whiteboard interview"])
        return self.rng.choice(["Top-30 visit", "Private workout", "Position workout", "Interview"])

    @staticmethod
    def _interest_level(
        rank: int,
        position: str,
        outcome_grade: int,
        risk_level: str,
    ) -> str:
        if rank <= 32 or (position.upper() == "QB" and rank <= 96):
            return "Heavy"
        if outcome_grade >= 70 and risk_level != "High":
            return "Heavy"
        if rank <= 160 or outcome_grade >= 60:
            return "Moderate"
        return "Late-round"

    def _note(self, workout_type: str, outcome_grade: int, true_grade: int) -> str:
        if workout_type == "Medical recheck":
            return "Medical/private follow-up created extra scouting context."
        if outcome_grade >= true_grade + 6:
            return "Private-workout impression came in stronger than the underlying grade."
        if outcome_grade <= true_grade - 6:
            return "Private-workout impression came in lighter than the underlying grade."
        return self.rng.choice(
            [
                "Private exposure roughly matched the generated player profile.",
                "Visit created a normal team-interest hook for future scouting.",
                "Workout/interview data gives scouts another modest signal.",
            ]
        )
