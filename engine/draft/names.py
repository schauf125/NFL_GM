"""Name-pool helpers for draft prospect generation."""

from __future__ import annotations

import random
import sqlite3
import unicodedata
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_NAME_DB = ROOT / "data" / "draft" / "names" / "name_pool.db"
UNITED_STATES = "United States"


def normalize_name_key(value: str) -> str:
    """Normalize a name for matching while preserving display names elsewhere."""
    ascii_value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    return "".join(ch for ch in ascii_value.upper() if "A" <= ch <= "Z")


@dataclass(frozen=True)
class GeneratedName:
    first_name: str
    last_name: str
    first_source: str
    last_source: str
    ethnicity_key: str
    ethnicity_label: str
    country: str
    is_international: bool

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}"


class NameGenerator:
    """Generate plausible player names from a compiled name-pool database."""

    def __init__(
        self,
        db_path: Path = DEFAULT_NAME_DB,
        *,
        seed: str | int | None = None,
    ) -> None:
        if not db_path.exists():
            raise FileNotFoundError(
                f"Name pool not found: {db_path}. Run tools/build_name_pool.py build first."
            )
        self.db_path = db_path
        self.rng = random.Random(seed)
        self.first_names = self._load_first_names()
        self.last_names = self._load_last_names()
        self.football_full_names = self._load_football_full_names()
        self.ethnicity_profiles = self._load_ethnicity_profiles()
        self.origin_countries = self._load_origin_countries()
        self.style_components = self._load_style_components()
        self.ethnicity_style_weights = self._load_style_weights(
            "ethnicity_name_style_weights",
            "ethnicity_key",
        )
        self.country_style_weights = self._load_style_weights(
            "country_name_style_weights",
            "country",
        )

    def generate(
        self,
        *,
        ethnicity_key: str | None = None,
        country: str | None = None,
        football_bias: float = 0.24,
        cultural_bias: float = 0.72,
        international_first_cultural_bias: float = 0.84,
        international_last_cultural_bias: float = 0.94,
        international_chance: float = 0.035,
        avoid_real_player_names: bool = True,
        max_attempts: int = 25,
    ) -> GeneratedName:
        """Return one generated name with fictional background metadata."""
        ethnicity_key, country, is_international = self._choose_background(
            ethnicity_key=ethnicity_key,
            country=country,
            international_chance=international_chance,
        )
        ethnicity_label = self.ethnicity_profiles[ethnicity_key]["label"]

        for _ in range(max_attempts):
            first_name, first_source = self._choose_name_component(
                "first",
                ethnicity_key=ethnicity_key,
                country=country,
                is_international=is_international,
                football_bias=football_bias,
                cultural_bias=international_first_cultural_bias if is_international else cultural_bias,
            )
            last_name, last_source = self._choose_name_component(
                "last",
                ethnicity_key=ethnicity_key,
                country=country,
                is_international=is_international,
                football_bias=football_bias,
                cultural_bias=international_last_cultural_bias if is_international else cultural_bias,
            )
            last_name, last_source = self._maybe_compound_last_name(
                last_name,
                last_source,
                ethnicity_key=ethnicity_key,
                country=country,
                is_international=is_international,
                football_bias=football_bias,
                cultural_bias=international_last_cultural_bias if is_international else cultural_bias,
            )
            full_key = normalize_name_key(f"{first_name} {last_name}")
            if not avoid_real_player_names or full_key not in self.football_full_names:
                return GeneratedName(
                    first_name=first_name,
                    last_name=last_name,
                    first_source=first_source,
                    last_source=last_source,
                    ethnicity_key=ethnicity_key,
                    ethnicity_label=ethnicity_label,
                    country=country,
                    is_international=is_international,
                )

        # If a very small or skewed name pool exhausts the retry budget, keep
        # fictionalization intact instead of returning a real football name.
        safe_first = f"{first_name}on"
        safe_last = f"{last_name}e"
        return GeneratedName(
            first_name=safe_first,
            last_name=safe_last,
            first_source=f"{first_source}:collision_fallback",
            last_source=f"{last_source}:collision_fallback",
            ethnicity_key=ethnicity_key,
            ethnicity_label=ethnicity_label,
            country=country,
            is_international=is_international,
        )

    def sample_ethnicity_mix(
        self,
        count: int,
        *,
        variance_scale: float = 1.0,
    ) -> dict[str, int]:
        """Sample class-level ethnicity counts around the configured targets."""
        if count < 1:
            return {}
        sampled: dict[str, float] = {}
        for key, profile in self.ethnicity_profiles.items():
            target = float(profile["target_pct"])
            sigma = float(profile["sigma_pct"]) * variance_scale
            value = self.rng.gauss(target, sigma)
            value = max(float(profile["min_pct"]), min(float(profile["max_pct"]), value))
            sampled[key] = value

        total = sum(sampled.values())
        normalized = {key: value / total for key, value in sampled.items()}
        raw_counts = {key: normalized[key] * count for key in normalized}
        counts = {key: int(raw_counts[key]) for key in raw_counts}
        remaining = count - sum(counts.values())
        remainders = sorted(
            raw_counts,
            key=lambda key: raw_counts[key] - counts[key],
            reverse=True,
        )
        for key in remainders[:remaining]:
            counts[key] += 1
        return counts

    def generate_class_names(
        self,
        count: int,
        *,
        ethnicity_variance: float = 1.0,
        **generate_kwargs: object,
    ) -> list[GeneratedName]:
        """Generate a class-sized list while keeping the group mix near targets."""
        mix = self.sample_ethnicity_mix(count, variance_scale=ethnicity_variance)
        ethnicity_keys = [
            ethnicity_key
            for ethnicity_key, ethnicity_count in mix.items()
            for _ in range(ethnicity_count)
        ]
        self.rng.shuffle(ethnicity_keys)
        return [
            self.generate(ethnicity_key=ethnicity_key, **generate_kwargs)
            for ethnicity_key in ethnicity_keys
        ]

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def _load_first_names(self) -> list[dict[str, object]]:
        with self._connect() as con:
            rows = con.execute(
                """
                SELECT
                    name,
                    weight,
                    football_weight,
                    source_flags
                FROM first_names
                WHERE gender = 'M'
                ORDER BY weight DESC, name
                """
            ).fetchall()
        return [
            {
                "name": row[0],
                "weight": max(1, int(row[1] or 0)),
                "football_weight": max(0, int(row[2] or 0)),
                "source": row[3],
            }
            for row in rows
        ]

    def _load_last_names(self) -> list[dict[str, object]]:
        with self._connect() as con:
            rows = con.execute(
                """
                SELECT
                    name,
                    weight,
                    football_weight,
                    source_flags
                FROM last_names
                ORDER BY weight DESC, name
                """
            ).fetchall()
        return [
            {
                "name": row[0],
                "weight": max(1, int(row[1] or 0)),
                "football_weight": max(0, int(row[2] or 0)),
                "source": row[3],
            }
            for row in rows
        ]

    def _load_football_full_names(self) -> set[str]:
        with self._connect() as con:
            rows = con.execute(
                """
                SELECT normalized_full_name
                FROM football_player_names
                WHERE normalized_full_name IS NOT NULL
                """
            ).fetchall()
        return {row[0] for row in rows}

    def _load_ethnicity_profiles(self) -> dict[str, dict[str, object]]:
        with self._connect() as con:
            rows = con.execute(
                """
                SELECT ethnicity_key, label, target_pct, sigma_pct, min_pct, max_pct
                FROM ethnicity_profiles
                ORDER BY target_pct DESC
                """
            ).fetchall()
        return {
            row[0]: {
                "label": row[1],
                "target_pct": float(row[2]),
                "sigma_pct": float(row[3]),
                "min_pct": float(row[4]),
                "max_pct": float(row[5]),
            }
            for row in rows
        }

    def _load_origin_countries(self) -> list[dict[str, object]]:
        with self._connect() as con:
            rows = con.execute(
                """
                SELECT country, weight, ethnicity_key, common_positions
                FROM international_origin_countries
                ORDER BY weight DESC, country
                """
            ).fetchall()
        return [
            {
                "country": row[0],
                "weight": max(1, int(row[1])),
                "ethnicity_key": row[2],
                "common_positions": row[3],
            }
            for row in rows
        ]

    def _load_style_components(self) -> dict[tuple[str, str], list[dict[str, object]]]:
        with self._connect() as con:
            rows = con.execute(
                """
                SELECT style_key, component_type, name, weight
                FROM name_style_components
                ORDER BY style_key, component_type, weight DESC, name
                """
            ).fetchall()
        components: dict[tuple[str, str], list[dict[str, object]]] = {}
        for style_key, component_type, name, weight in rows:
            components.setdefault((style_key, component_type), []).append(
                {"name": name, "weight": max(1, int(weight))}
            )
        return components

    def _load_style_weights(
        self,
        table_name: str,
        owner_column: str,
    ) -> dict[tuple[str, str], list[tuple[str, int]]]:
        with self._connect() as con:
            rows = con.execute(
                f"""
                SELECT {owner_column}, component_type, style_key, weight
                FROM {table_name}
                ORDER BY weight DESC, style_key
                """
            ).fetchall()
        weights: dict[tuple[str, str], list[tuple[str, int]]] = {}
        for owner, component_type, style_key, weight in rows:
            weights.setdefault((owner, component_type), []).append(
                (style_key, max(1, int(weight)))
            )
        return weights

    def _choose_background(
        self,
        *,
        ethnicity_key: str | None,
        country: str | None,
        international_chance: float,
    ) -> tuple[str, str, bool]:
        if ethnicity_key and ethnicity_key not in self.ethnicity_profiles:
            raise ValueError(f"Unknown ethnicity key: {ethnicity_key}")

        if country:
            if country == UNITED_STATES:
                return ethnicity_key or self._choose_ethnicity(), UNITED_STATES, False
            country_info = self._country_by_name(country)
            if not country_info:
                raise ValueError(f"Unknown international origin country: {country}")
            chosen_ethnicity = ethnicity_key or str(country_info["ethnicity_key"])
            return chosen_ethnicity, str(country_info["country"]), True

        if self.origin_countries and self.rng.random() < international_chance:
            country_info = self._choose_country()
            chosen_ethnicity = ethnicity_key or str(country_info["ethnicity_key"])
            return chosen_ethnicity, str(country_info["country"]), True

        return ethnicity_key or self._choose_ethnicity(), UNITED_STATES, False

    def _choose_ethnicity(self) -> str:
        keys = list(self.ethnicity_profiles)
        weights = [float(self.ethnicity_profiles[key]["target_pct"]) for key in keys]
        return self.rng.choices(keys, weights=weights, k=1)[0]

    def _choose_country(self, *, ethnicity_key: str | None = None) -> dict[str, object]:
        countries = self.origin_countries
        if ethnicity_key:
            matching = [
                country
                for country in countries
                if country["ethnicity_key"] == ethnicity_key
            ]
            if matching:
                countries = matching
        weights = [int(country["weight"]) for country in countries]
        return self.rng.choices(countries, weights=weights, k=1)[0]

    def ethnicity_key_for_country(self, country: str) -> str | None:
        country_info = self._country_by_name(country)
        if not country_info:
            return None
        return str(country_info["ethnicity_key"])

    def _country_by_name(self, country: str) -> dict[str, object] | None:
        key = country.lower()
        for row in self.origin_countries:
            if str(row["country"]).lower() == key:
                return row
        return None

    def _choose_name_component(
        self,
        component_type: str,
        *,
        ethnicity_key: str,
        country: str,
        is_international: bool,
        football_bias: float,
        cultural_bias: float,
    ) -> tuple[str, str]:
        style_weights = self.ethnicity_style_weights.get((ethnicity_key, component_type), [])
        if is_international:
            style_weights = self.country_style_weights.get(
                (country, component_type),
                style_weights,
            )

        if style_weights and self.rng.random() < cultural_bias:
            style_key = self._weighted_style(style_weights)
            if style_key == "football":
                return self._choose_component(
                    self._base_rows(component_type),
                    football_bias=1.0,
                )
            if style_key == "us_general":
                name, source = self._choose_component(
                    self._base_rows(component_type),
                    football_bias=0.0,
                )
                return name, f"{source}:us_general"

            components = self.style_components.get((style_key, component_type), [])
            if components:
                weights = [int(row["weight"]) for row in components]
                choice = self.rng.choices(components, weights=weights, k=1)[0]
                return str(choice["name"]), f"style:{style_key}"

        return self._choose_component(
            self._base_rows(component_type),
            football_bias=football_bias,
        )

    def _maybe_compound_last_name(
        self,
        last_name: str,
        last_source: str,
        *,
        ethnicity_key: str,
        country: str,
        is_international: bool,
        football_bias: float,
        cultural_bias: float,
    ) -> tuple[str, str]:
        if "-" in last_name or " " in last_name:
            return last_name, last_source
        chance = 0.0
        if is_international:
            chance = {
                "Mexico": 0.065,
                "Brazil": 0.050,
                "Philippines": 0.045,
                "Canada": 0.018,
                "United Kingdom": 0.015,
                "Australia": 0.012,
                "France": 0.018,
            }.get(country, 0.006)
        elif ethnicity_key == "hispanic_latino":
            chance = 0.018
        if chance <= 0 or self.rng.random() >= chance:
            return last_name, last_source
        second_last, second_source = self._choose_name_component(
            "last",
            ethnicity_key=ethnicity_key,
            country=country,
            is_international=is_international,
            football_bias=football_bias * 0.5,
            cultural_bias=cultural_bias,
        )
        if (
            second_last == last_name
            or "-" in second_last
            or " " in second_last
            or len(second_last) < 3
        ):
            return last_name, last_source
        return f"{last_name}-{second_last}", f"{last_source}+{second_source}:compound"

    def _base_rows(self, component_type: str) -> list[dict[str, object]]:
        if component_type == "first":
            return self.first_names
        if component_type == "last":
            return self.last_names
        raise ValueError(f"Unknown component type: {component_type}")

    def _weighted_style(self, style_weights: list[tuple[str, int]]) -> str:
        styles = [style for style, _ in style_weights]
        weights = [weight for _, weight in style_weights]
        return self.rng.choices(styles, weights=weights, k=1)[0]

    def _choose_component(
        self,
        rows: list[dict[str, object]],
        *,
        football_bias: float,
    ) -> tuple[str, str]:
        football_rows = [row for row in rows if int(row["football_weight"]) > 0]
        use_football = football_rows and self.rng.random() < football_bias
        pool = football_rows if use_football else rows
        weight_key = "football_weight" if use_football else "weight"
        weights = [int(row[weight_key]) for row in pool]
        choice = self.rng.choices(pool, weights=weights, k=1)[0]
        source = "football" if use_football else str(choice["source"])
        return str(choice["name"]), source
