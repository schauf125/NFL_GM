"""College and age generation for draft prospects."""

from __future__ import annotations

import random
import sqlite3
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_COLLEGE_DB = ROOT / "data" / "draft" / "colleges" / "college_pool.db"
AGE_BUCKET_END_RANKS = {
    "round_1": 32,
    "round_2_3": 96,
    "round_4_5": 160,
    "round_6_7": 256,
}


@dataclass(frozen=True)
class CollegeProfile:
    age: int
    college: str
    college_tier: str


class CollegeGenerator:
    """Generate a plausible draft age and college/development source."""

    def __init__(
        self,
        db_path: Path = DEFAULT_COLLEGE_DB,
        *,
        seed: str | int | None = None,
    ) -> None:
        if not db_path.exists():
            raise FileNotFoundError(
                f"College pool not found: {db_path}. Run tools/build_college_pool.py build first."
            )
        self.db_path = db_path
        self.rng = random.Random(seed)
        self.colleges = self._load_colleges()
        self.age_weights = self._load_age_weights()
        self.international_sources = self._load_international_sources()

    def generate(
        self,
        *,
        rank: int | None = None,
        is_international: bool = False,
        international_college_chance: float = 0.28,
        age: int | None = None,
        tier_weights: dict[str, float] | None = None,
    ) -> CollegeProfile:
        if age is None:
            age = self._choose_age(rank)
        if is_international and self.rng.random() < international_college_chance:
            source = self._weighted_choice(self.international_sources)
            return CollegeProfile(age=age, college=source, college_tier="International")
        college_row = self._weighted_choice_row(self.colleges, tier_weights=tier_weights)
        return CollegeProfile(
            age=age,
            college=str(college_row["college"]),
            college_tier=str(college_row["tier"]),
        )

    def ranked_age_plan(self, count: int) -> list[int]:
        """Return a shuffled age plan for each draft-board bucket."""
        ages: list[int] = []
        start_rank = 1
        while start_rank <= count:
            bucket = self._age_bucket_for_rank(start_rank)
            end_rank = min(count, AGE_BUCKET_END_RANKS.get(bucket, count))
            bucket_count = end_rank - start_rank + 1
            bucket_ages = self._sample_age_bucket(bucket, bucket_count)
            self.rng.shuffle(bucket_ages)
            ages.extend(bucket_ages)
            start_rank = end_rank + 1
        return ages

    def _load_colleges(self) -> list[dict[str, object]]:
        with sqlite3.connect(self.db_path) as con:
            con.row_factory = sqlite3.Row
            rows = con.execute(
                """
                SELECT college, weight, tier
                FROM college_pool
                ORDER BY weight DESC, college
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def _load_age_weights(self) -> list[dict[str, object]]:
        with sqlite3.connect(self.db_path) as con:
            con.row_factory = sqlite3.Row
            rows = con.execute(
                "SELECT bucket, age, weight FROM age_weights ORDER BY bucket, age"
            ).fetchall()
        return [dict(row) for row in rows]

    def _load_international_sources(self) -> dict[str, float]:
        with sqlite3.connect(self.db_path) as con:
            rows = con.execute(
                "SELECT source_name, weight FROM international_development_sources"
            ).fetchall()
        return {row[0]: float(row[1]) for row in rows}

    def _choose_age(self, rank: int | None) -> int:
        bucket = self._age_bucket_for_rank(rank)
        rows = self._age_rows_for_bucket(bucket)
        ages = [int(row["age"]) for row in rows]
        weights = [float(row["weight"]) for row in rows]
        return self.rng.choices(ages, weights=weights, k=1)[0]

    def _sample_age_bucket(self, bucket: str, count: int) -> list[int]:
        rows = self._age_rows_for_bucket(bucket)
        total_weight = sum(float(row["weight"]) for row in rows)
        raw_counts = {
            int(row["age"]): float(row["weight"]) / total_weight * count
            for row in rows
        }
        counts = {age: int(raw_count) for age, raw_count in raw_counts.items()}
        remaining = count - sum(counts.values())
        if remaining > 0:
            ages = list(raw_counts)
            fractions = [raw_counts[age] - counts[age] for age in ages]
            if sum(fractions) <= 0:
                fractions = [float(row["weight"]) for row in rows]
            for age in self._weighted_choices_without_replacement(
                ages,
                fractions,
                remaining,
            ):
                counts[age] += 1
        return [
            age
            for age, age_count in counts.items()
            for _ in range(age_count)
        ]

    def _age_rows_for_bucket(self, bucket: str) -> list[dict[str, object]]:
        rows = [row for row in self.age_weights if row["bucket"] == bucket]
        if not rows:
            rows = [row for row in self.age_weights if row["bucket"] == "round_4_5"]
        return rows

    @staticmethod
    def _age_bucket_for_rank(rank: int | None) -> str:
        if rank is None:
            return "round_4_5"
        if rank <= 32:
            return "round_1"
        if rank <= 96:
            return "round_2_3"
        if rank <= 160:
            return "round_4_5"
        if rank <= 256:
            return "round_6_7"
        return "leftover"

    def _weighted_choice_row(
        self,
        rows: list[dict[str, object]],
        *,
        tier_weights: dict[str, float] | None = None,
    ) -> dict[str, object]:
        weights = [
            float(row["weight"]) * float((tier_weights or {}).get(str(row.get("tier")), 1.0))
            for row in rows
        ]
        return self.rng.choices(rows, weights=weights, k=1)[0]

    def _weighted_choice(self, weights: dict[str, float]) -> str:
        names = list(weights)
        values = [float(weights[name]) for name in names]
        return self.rng.choices(names, weights=values, k=1)[0]

    def _weighted_choices_without_replacement(
        self,
        values: list[int],
        weights: list[float],
        count: int,
    ) -> list[int]:
        chosen: list[int] = []
        available_values = values[:]
        available_weights = [float(weight) for weight in weights]
        for _ in range(min(count, len(available_values))):
            index = self.rng.choices(
                range(len(available_values)),
                weights=available_weights,
                k=1,
            )[0]
            chosen.append(available_values.pop(index))
            available_weights.pop(index)
            if not available_values or sum(available_weights) <= 0:
                break
        if len(chosen) < count:
            chosen.extend(self.rng.choices(values, weights=weights, k=count - len(chosen)))
        return chosen
