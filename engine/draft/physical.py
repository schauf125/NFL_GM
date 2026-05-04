"""Physical trait generation for draft prospects."""

from __future__ import annotations

import math
import random
import sqlite3
from dataclasses import dataclass
from math import gcd
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PHYSICAL_DB = ROOT / "data" / "draft" / "physical" / "physical_profiles.db"

POSITION_WEIGHT_ADJUSTMENTS = {
    # The safety source pool skews closer to free-safety bodies. Strong safeties
    # should usually carry a little more box-player mass.
    "SS": 6,
    # Long snappers are often grouped with lighter special-team bodies in sparse
    # source data; keep the generated floor closer to real roster weights.
    "LS": 15,
}


@dataclass(frozen=True)
class PhysicalTraits:
    position: str
    height_in: int
    weight_lbs: int
    arm_length_in: float
    hand_size_in: float
    height_z: float
    weight_z: float
    arm_z: float
    hand_z: float
    is_height_outlier: bool
    is_weight_outlier: bool
    is_arm_outlier: bool
    is_hand_outlier: bool

    @property
    def is_outlier(self) -> bool:
        return (
            self.is_height_outlier
            or self.is_weight_outlier
            or self.is_arm_outlier
            or self.is_hand_outlier
        )


def format_height(height_in: int) -> str:
    return f"{height_in // 12}'{height_in % 12}\""


def format_measurement(value: float) -> str:
    rounded_eighths = round(value * 8)
    whole = rounded_eighths // 8
    numerator = rounded_eighths % 8
    if numerator == 0:
        return f'{whole}"'
    divisor = gcd(numerator, 8)
    return f'{whole} {numerator // divisor}/{8 // divisor}"'


class PhysicalProfileGenerator:
    """Sample position-specific NFL body measurements."""

    def __init__(
        self,
        db_path: Path = DEFAULT_PHYSICAL_DB,
        *,
        seed: str | int | None = None,
    ) -> None:
        if not db_path.exists():
            raise FileNotFoundError(
                f"Physical profile DB not found: {db_path}. "
                "Run tools/build_physical_profiles.py build first."
            )
        self.db_path = db_path
        self.rng = random.Random(seed)
        self.profiles = self._load_profiles()

    def generate(
        self,
        position: str,
        *,
        outlier_chance: float = 0.045,
    ) -> PhysicalTraits:
        profile = self._profile(position)
        per_dimension_outlier_chance = 1.0 - (
            max(0.0, 1.0 - outlier_chance) ** 0.25
        )
        height_z, is_height_outlier = self._sample_z(per_dimension_outlier_chance)
        independent_z, is_weight_outlier = self._sample_z(per_dimension_outlier_chance)
        corr = float(profile["height_weight_corr"])
        weight_z = corr * height_z + math.sqrt(max(0.0, 1.0 - corr**2)) * independent_z
        independent_arm_z, is_arm_outlier = self._sample_z(per_dimension_outlier_chance)
        independent_hand_z, is_hand_outlier = self._sample_z(per_dimension_outlier_chance)
        arm_height_corr = float(profile["arm_height_corr"])
        arm_z = (
            arm_height_corr * height_z
            + math.sqrt(max(0.0, 1.0 - arm_height_corr**2)) * independent_arm_z
        )
        hand_height_corr = float(profile["hand_height_corr"])
        hand_weight_corr = float(profile["hand_weight_corr"])
        hand_height_coef = 0.65 * hand_height_corr
        hand_weight_coef = 0.35 * hand_weight_corr
        hand_signal_variance = (
            hand_height_coef**2
            + hand_weight_coef**2
            + 2 * hand_height_coef * hand_weight_coef * corr
        )
        hand_residual = math.sqrt(max(0.10, 1.0 - hand_signal_variance))
        hand_z = (
            hand_height_coef * height_z
            + hand_weight_coef * weight_z
            + hand_residual * independent_hand_z
        )

        height = round(
            float(profile["height_mean"]) + height_z * float(profile["height_sd"])
        )
        weight = round(
            float(profile["weight_mean"]) + weight_z * float(profile["weight_sd"])
        )
        arm_length = (
            float(profile["arm_length_mean"])
            + arm_z * float(profile["arm_length_sd"])
        )
        hand_size = (
            float(profile["hand_size_mean"])
            + hand_z * float(profile["hand_size_sd"])
        )
        height = self._clamp(
            height,
            int(profile["gen_height_min"]),
            int(profile["gen_height_max"]),
        )
        weight = self._clamp(
            weight,
            int(profile["gen_weight_min"]),
            int(profile["gen_weight_max"]),
        )
        weight = self._clamp(
            weight + POSITION_WEIGHT_ADJUSTMENTS.get(str(profile["position"]).upper(), 0),
            int(profile["gen_weight_min"]),
            int(profile["gen_weight_max"]),
        )
        arm_length = self._clamp_float(
            arm_length,
            float(profile["gen_arm_length_min"]),
            float(profile["gen_arm_length_max"]),
        )
        hand_size = self._clamp_float(
            hand_size,
            float(profile["gen_hand_size_min"]),
            float(profile["gen_hand_size_max"]),
        )
        weight = self._round_to_nearest(weight, 1)
        arm_length = self._round_to_nearest_float(arm_length, 0.125)
        hand_size = self._round_to_nearest_float(hand_size, 0.125)
        return PhysicalTraits(
            position=str(profile["position"]),
            height_in=height,
            weight_lbs=weight,
            arm_length_in=arm_length,
            hand_size_in=hand_size,
            height_z=height_z,
            weight_z=weight_z,
            arm_z=arm_z,
            hand_z=hand_z,
            is_height_outlier=is_height_outlier,
            is_weight_outlier=is_weight_outlier,
            is_arm_outlier=is_arm_outlier,
            is_hand_outlier=is_hand_outlier,
        )

    def _load_profiles(self) -> dict[str, dict[str, object]]:
        with sqlite3.connect(self.db_path) as con:
            con.row_factory = sqlite3.Row
            rows = con.execute("SELECT * FROM physical_profiles").fetchall()
        return {str(row["position"]).upper(): dict(row) for row in rows}

    def _profile(self, position: str) -> dict[str, object]:
        key = position.upper()
        if key not in self.profiles:
            raise ValueError(f"Unknown position for physical profile: {position}")
        return self.profiles[key]

    def _sample_z(self, outlier_chance: float) -> tuple[float, bool]:
        if self.rng.random() < outlier_chance:
            direction = -1 if self.rng.random() < 0.5 else 1
            return direction * self.rng.uniform(2.0, 3.25), True
        while True:
            value = self.rng.gauss(0.0, 1.0)
            if -2.35 <= value <= 2.35:
                return value, False

    @staticmethod
    def _clamp(value: int, low: int, high: int) -> int:
        return max(low, min(high, value))

    @staticmethod
    def _clamp_float(value: float, low: float, high: float) -> float:
        return max(low, min(high, value))

    @staticmethod
    def _round_to_nearest(value: int, nearest: int) -> int:
        return int(round(value / nearest) * nearest)

    @staticmethod
    def _round_to_nearest_float(value: float, nearest: float) -> float:
        return round(value / nearest) * nearest
