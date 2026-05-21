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

DISTINCTIVE_FIRST_NAMES: tuple[tuple[str, float], ...] = (
    ("Ace", 8.0),
    ("Bam", 3.0),
    ("Banks", 5.0),
    ("Blaze", 4.0),
    ("Bishop", 5.0),
    ("Boogie", 1.0),
    ("Boss", 2.0),
    ("Bumper", 1.0),
    ("Cannon", 4.0),
    ("Cash", 7.0),
    ("Creed", 5.0),
    ("Dash", 3.0),
    ("Deuce", 3.0),
    ("Flash", 1.0),
    ("Hawk", 2.0),
    ("Jet", 3.0),
    ("King", 7.0),
    ("Kool-Aid", 0.7),
    ("Legend", 3.0),
    ("Major", 5.0),
    ("Noble", 4.0),
    ("Ridge", 3.0),
    ("Rocket", 2.0),
    ("Royal", 4.0),
    ("Seven", 2.0),
    ("Slate", 3.0),
    ("Steel", 3.0),
    ("Stone", 5.0),
    ("Storm", 3.0),
    ("Summit", 2.0),
    ("Tank", 3.0),
    ("Titan", 2.0),
    ("Truth", 2.0),
    ("Zephyr", 1.0),
)

INITIAL_FIRST_NAMES: tuple[tuple[str, float], ...] = (
    ("A.J.", 8.0),
    ("C.J.", 9.0),
    ("D.J.", 9.0),
    ("J.J.", 5.0),
    ("J.T.", 4.0),
    ("K.J.", 6.0),
    ("R.J.", 4.0),
    ("T.J.", 7.0),
)

FAMILY_SUFFIXES: tuple[tuple[str, float], ...] = (
    ("Jr.", 62.0),
    ("II", 18.0),
    ("III", 17.0),
    ("IV", 3.0),
)

DISTINCTIVE_LAST_NAME_MULTIPLIERS: tuple[tuple[str, float], ...] = (
    ("alliterative", 1.45),
    ("short", 1.18),
    ("compound", 0.55),
)

POSITION_DISTINCTIVE_MULTIPLIERS: dict[str, dict[str, float]] = {
    "QB": {"Major": 1.25, "Legend": 1.15, "King": 1.12, "Boss": 0.75, "Tank": 0.45, "Bam": 0.55},
    "RB": {"Tank": 1.85, "Bam": 1.55, "Deuce": 1.35, "Dash": 1.25, "Jet": 1.2, "Flash": 1.2},
    "WR": {"Jet": 1.65, "Dash": 1.45, "Flash": 1.45, "Rocket": 1.35, "Tank": 0.55, "Bumper": 0.4},
    "TE": {"Tank": 1.25, "Stone": 1.2, "Bishop": 1.15, "Flash": 0.55},
    "OT": {"Tank": 1.45, "Bumper": 1.35, "Cannon": 1.25, "Stone": 1.25, "Dash": 0.45, "Flash": 0.35},
    "OG": {"Tank": 1.55, "Bumper": 1.45, "Cannon": 1.25, "Stone": 1.25, "Dash": 0.4, "Flash": 0.35},
    "C": {"Bumper": 1.35, "Boss": 1.2, "Stone": 1.2, "Jet": 0.55, "Flash": 0.45},
    "IDL": {"Tank": 1.75, "Bam": 1.5, "Bumper": 1.35, "Cannon": 1.3, "Stone": 1.25, "Jet": 0.45},
    "EDGE": {"Hawk": 1.45, "Cannon": 1.25, "Storm": 1.2, "Flash": 1.15, "Tank": 1.1},
    "ILB": {"Bumper": 1.55, "Boss": 1.35, "Tank": 1.25, "Hawk": 1.2, "Jet": 0.55},
    "LB": {"Bumper": 1.55, "Boss": 1.35, "Tank": 1.25, "Hawk": 1.2, "Jet": 0.55},
    "CB": {"Jet": 1.5, "Dash": 1.35, "Flash": 1.3, "Hawk": 1.2, "Tank": 0.45, "Bumper": 0.35},
    "NB": {"Jet": 1.45, "Dash": 1.35, "Flash": 1.3, "Hawk": 1.15, "Tank": 0.45},
    "FS": {"Hawk": 1.45, "Ace": 1.15, "Jet": 1.1, "Bumper": 0.45},
    "SS": {"Hawk": 1.35, "Boss": 1.25, "Tank": 1.15, "Bumper": 1.1, "Flash": 0.55},
}

REGION_STYLE_MULTIPLIERS: dict[str, dict[str, dict[str, float]]] = {
    "Texas": {
        "first": {"hispanic_latino": 1.18, "football": 1.12, "us_general": 0.94},
        "last": {"hispanic_latino": 1.22, "football": 1.08, "us_general": 0.95},
    },
    "Southeast": {
        "first": {"african_american": 1.16, "football": 1.10, "caribbean": 1.08, "us_general": 0.96},
        "last": {"african_american": 1.12, "football": 1.10, "caribbean": 1.06, "us_general": 0.96},
    },
    "West": {
        "first": {"hispanic_latino": 1.12, "asian": 1.12, "polynesian": 1.16, "hawaiian": 1.22, "football": 1.04, "us_general": 0.96},
        "last": {"hispanic_latino": 1.14, "asian": 1.14, "polynesian": 1.18, "hawaiian": 1.24, "us_general": 0.96},
    },
    "Northeast": {
        "first": {"anglo": 1.10, "european": 1.10, "us_general": 1.04},
        "last": {"anglo": 1.12, "european": 1.12, "us_general": 1.02},
    },
    "Midwest": {
        "first": {"anglo": 1.08, "european": 1.08, "football": 1.05, "us_general": 1.03},
        "last": {"anglo": 1.10, "european": 1.10, "football": 1.04, "us_general": 1.02},
    },
    "Plains": {
        "first": {"american_indian": 1.18, "anglo": 1.06, "football": 1.04},
        "last": {"american_indian": 1.22, "anglo": 1.05, "football": 1.03},
    },
}

STATE_STYLE_MULTIPLIERS: dict[str, dict[str, dict[str, float]]] = {
    "HI": {
        "first": {"hawaiian": 2.4, "polynesian": 1.9, "asian": 1.25, "us_general": 0.72},
        "last": {"hawaiian": 2.6, "polynesian": 2.1, "asian": 1.25, "us_general": 0.70},
    },
    "UT": {
        "first": {"polynesian": 1.45, "hawaiian": 1.25, "anglo": 1.08},
        "last": {"polynesian": 1.55, "hawaiian": 1.28, "anglo": 1.08},
    },
    "CA": {
        "first": {"hispanic_latino": 1.18, "asian": 1.18, "polynesian": 1.12},
        "last": {"hispanic_latino": 1.22, "asian": 1.20, "polynesian": 1.12},
    },
    "TX": {
        "first": {"hispanic_latino": 1.22, "football": 1.10},
        "last": {"hispanic_latino": 1.28, "football": 1.08},
    },
    "FL": {
        "first": {"hispanic_latino": 1.14, "caribbean": 1.22, "football": 1.08},
        "last": {"hispanic_latino": 1.16, "caribbean": 1.28, "football": 1.08},
    },
    "LA": {
        "first": {"african_american": 1.18, "caribbean": 1.10, "football": 1.08},
        "last": {"african_american": 1.16, "caribbean": 1.10, "football": 1.08},
    },
    "OK": {
        "first": {"american_indian": 1.35, "football": 1.05},
        "last": {"american_indian": 1.45, "football": 1.04},
    },
    "AZ": {
        "first": {"hispanic_latino": 1.22, "american_indian": 1.20},
        "last": {"hispanic_latino": 1.25, "american_indian": 1.25},
    },
    "NM": {
        "first": {"hispanic_latino": 1.25, "american_indian": 1.22},
        "last": {"hispanic_latino": 1.30, "american_indian": 1.25},
    },
}

PRIMARY_CULTURAL_STYLE_BY_ETHNICITY: dict[str, set[str]] = {
    "black_african_american": {"african_american", "west_african", "caribbean"},
    "white": {"anglo", "european", "canadian", "australian", "german", "british"},
    "hispanic_latino": {"hispanic_latino", "brazilian"},
    "native_hawaiian_pacific_islander": {"polynesian", "hawaiian", "samoan"},
    "asian": {"asian", "south_asian", "filipino"},
    "american_indian_alaska_native": {"native_american"},
}

INCOMPATIBLE_NAME_STYLE_MULTIPLIERS: dict[str, dict[str, dict[str, float]]] = {
    "white": {
        "first": {
            "african_american": 0.025,
            "west_african": 0.025,
            "caribbean": 0.025,
            "polynesian": 0.10,
            "hawaiian": 0.10,
            "samoan": 0.08,
            "asian": 0.18,
            "south_asian": 0.14,
            "native_american": 0.14,
        },
        "last": {
            "west_african": 0.08,
            "polynesian": 0.10,
            "hawaiian": 0.10,
            "samoan": 0.08,
            "asian": 0.16,
            "south_asian": 0.12,
            "native_american": 0.18,
        },
    },
    "black_african_american": {
        "first": {
            "anglo": 0.58,
            "european": 0.42,
            "german": 0.28,
            "asian": 0.16,
            "south_asian": 0.12,
            "polynesian": 0.10,
            "hawaiian": 0.10,
            "samoan": 0.08,
        },
        "last": {
            "asian": 0.15,
            "south_asian": 0.12,
            "polynesian": 0.12,
            "hawaiian": 0.12,
            "samoan": 0.10,
        },
    },
}

STYLE_COMPATIBILITY_OUTLIER_RATE: dict[str, float] = {
    "first": 0.004,
    "last": 0.025,
}

FOOTBALL_STYLE_WEIGHT_MULTIPLIER = 0.25
FOOTBALL_DIRECT_BIAS_MULTIPLIER = 0.25
FOOTBALL_BASE_POOL_WEIGHT_MULTIPLIER = 0.35


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
        self.component_style_lookup = self._build_component_style_lookup()
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
        position: str | None = None,
        hometown_state: str | None = None,
        hometown_region: str | None = None,
        football_bias: float = 0.08,
        cultural_bias: float = 0.78,
        international_first_cultural_bias: float = 0.84,
        international_last_cultural_bias: float = 0.94,
        international_chance: float = 0.035,
        distinctive_name_chance: float = 0.013,
        initial_name_chance: float = 0.008,
        family_suffix_chance: float = 0.065,
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
        domestic_cultural_bias = self._domestic_cultural_bias(
            ethnicity_key,
            base_bias=cultural_bias,
        )

        for _ in range(max_attempts):
            first_name, first_source = self._choose_name_component(
                "first",
                ethnicity_key=ethnicity_key,
                country=country,
                is_international=is_international,
                hometown_state=hometown_state,
                hometown_region=hometown_region,
                football_bias=football_bias,
                cultural_bias=international_first_cultural_bias if is_international else domestic_cultural_bias,
            )
            last_name, last_source = self._choose_name_component(
                "last",
                ethnicity_key=ethnicity_key,
                country=country,
                is_international=is_international,
                hometown_state=hometown_state,
                hometown_region=hometown_region,
                football_bias=football_bias,
                cultural_bias=international_last_cultural_bias if is_international else domestic_cultural_bias,
            )
            last_name, last_source = self._maybe_compound_last_name(
                last_name,
                last_source,
                ethnicity_key=ethnicity_key,
                country=country,
                is_international=is_international,
                hometown_state=hometown_state,
                hometown_region=hometown_region,
                football_bias=football_bias,
                cultural_bias=international_last_cultural_bias if is_international else domestic_cultural_bias,
            )
            first_name, first_source = self._maybe_initial_first_name(
                first_name,
                first_source,
                is_international=is_international,
                chance=initial_name_chance,
            )
            first_name, first_source = self._maybe_distinctive_first_name(
                first_name,
                first_source,
                last_name=last_name,
                position=position,
                hometown_state=hometown_state,
                hometown_region=hometown_region,
                is_international=is_international,
                chance=distinctive_name_chance,
            )
            last_name, last_source = self._maybe_family_suffix(
                last_name,
                last_source,
                is_international=is_international,
                chance=family_suffix_chance,
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
        names: list[GeneratedName] = []
        first_counts: dict[str, int] = {}
        soft_cap = int(generate_kwargs.pop("max_common_first_repeats", 5) or 5)
        for ethnicity_key in ethnicity_keys:
            chosen: GeneratedName | None = None
            for attempt in range(6):
                candidate = self.generate(ethnicity_key=ethnicity_key, **generate_kwargs)
                first_key = normalize_name_key(candidate.first_name)
                distinctive = (
                    "distinctive_flair" in candidate.first_source
                    or "initials" in candidate.first_source
                )
                current_count = first_counts.get(first_key, 0)
                if distinctive or current_count < soft_cap or attempt >= 5:
                    chosen = candidate
                    break
            if chosen is None:
                chosen = candidate
            first_counts[normalize_name_key(chosen.first_name)] = first_counts.get(normalize_name_key(chosen.first_name), 0) + 1
            names.append(chosen)
        return names

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

    @staticmethod
    def _domestic_cultural_bias(ethnicity_key: str, *, base_bias: float) -> float:
        """Keep U.S.-born names mostly coherent while preserving common outliers."""
        multipliers = {
            "hispanic_latino": 1.10,
            "native_hawaiian_pacific_islander": 1.13,
            "asian": 1.08,
            "american_indian_alaska_native": 1.10,
            "black_african_american": 1.03,
            "white": 1.00,
            "multiracial": 0.96,
            "other_unknown": 0.94,
        }
        return min(0.92, max(0.45, base_bias * multipliers.get(ethnicity_key, 1.0)))

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

    @staticmethod
    def _apply_hometown_style_context(
        style_weights: list[tuple[str, int]],
        *,
        component_type: str,
        hometown_state: str | None,
        hometown_region: str | None,
    ) -> list[tuple[str, int]]:
        if not style_weights:
            return style_weights
        adjusted = {style: float(weight) for style, weight in style_weights}
        for multipliers in (
            REGION_STYLE_MULTIPLIERS.get(str(hometown_region or ""), {}).get(component_type, {}),
            STATE_STYLE_MULTIPLIERS.get(str(hometown_state or "").upper(), {}).get(component_type, {}),
        ):
            for style, multiplier in multipliers.items():
                if style in adjusted:
                    adjusted[style] *= float(multiplier)
        return [(style, max(1, int(round(weight)))) for style, weight in adjusted.items()]

    def _choose_name_component(
        self,
        component_type: str,
        *,
        ethnicity_key: str,
        country: str,
        is_international: bool,
        hometown_state: str | None,
        hometown_region: str | None,
        football_bias: float,
        cultural_bias: float,
    ) -> tuple[str, str]:
        style_weights = self.ethnicity_style_weights.get((ethnicity_key, component_type), [])
        if is_international:
            style_weights = self.country_style_weights.get(
                (country, component_type),
                style_weights,
            )
        else:
            style_weights = self._apply_hometown_style_context(
                style_weights,
                component_type=component_type,
                hometown_state=hometown_state,
                hometown_region=hometown_region,
            )

        if style_weights and self.rng.random() < cultural_bias:
            style_key = self._weighted_style(style_weights)
            if style_key == "football":
                return self._choose_component(
                    self._base_rows(component_type),
                    football_bias=1.0,
                    component_type=component_type,
                    ethnicity_key=ethnicity_key,
                )
            if style_key == "us_general":
                name, source = self._choose_component(
                    self._base_rows(component_type),
                    football_bias=0.0,
                    component_type=component_type,
                    ethnicity_key=ethnicity_key,
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
            component_type=component_type,
            ethnicity_key=ethnicity_key,
        )

    def _maybe_compound_last_name(
        self,
        last_name: str,
        last_source: str,
        *,
        ethnicity_key: str,
        country: str,
        is_international: bool,
        hometown_state: str | None,
        hometown_region: str | None,
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
        elif ethnicity_key == "multiracial":
            chance = 0.007
        elif ethnicity_key in {"black_african_american", "white"}:
            chance = 0.003
        else:
            chance = 0.004
        if chance <= 0 or self.rng.random() >= chance:
            return last_name, last_source
        second_last, second_source = self._choose_name_component(
            "last",
            ethnicity_key=ethnicity_key,
            country=country,
            is_international=is_international,
            hometown_state=hometown_state,
            hometown_region=hometown_region,
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

    def _maybe_distinctive_first_name(
        self,
        first_name: str,
        first_source: str,
        *,
        last_name: str,
        position: str | None,
        hometown_state: str | None,
        hometown_region: str | None,
        is_international: bool,
        chance: float,
    ) -> tuple[str, str]:
        if chance <= 0:
            return first_name, first_source
        adjusted_chance = chance * (0.35 if is_international else 1.0)
        if self.rng.random() >= adjusted_chance:
            return first_name, first_source
        candidates = dict(DISTINCTIVE_FIRST_NAMES)
        position_key = str(position or "").upper()
        for name, multiplier in POSITION_DISTINCTIVE_MULTIPLIERS.get(position_key, {}).items():
            if name in candidates:
                candidates[name] *= float(multiplier)
        state = str(hometown_state or "").upper()
        region = str(hometown_region or "")
        if state in {"HI", "CA", "WA", "OR", "UT"}:
            for name in ("Makoa", "Royal", "King", "Jet", "Storm"):
                if name in candidates:
                    candidates[name] *= 1.12
        if region in {"Southeast", "Texas"}:
            for name in ("Cash", "King", "Cannon", "Boss", "Tank", "Bam"):
                if name in candidates:
                    candidates[name] *= 1.10
        if region in {"Northeast", "Midwest"}:
            for name in ("Ace", "Major", "Stone", "Bishop", "Creed"):
                if name in candidates:
                    candidates[name] *= 1.08
        last_key = normalize_name_key(last_name)
        if last_key:
            for style, multiplier in DISTINCTIVE_LAST_NAME_MULTIPLIERS:
                if style == "alliterative":
                    for name in list(candidates):
                        if normalize_name_key(name).startswith(last_key[0]):
                            candidates[name] *= multiplier
                elif style == "short" and len(last_key) <= 5:
                    for name in ("Ace", "Cash", "Dash", "Jet", "King", "Royal", "Stone"):
                        if name in candidates:
                            candidates[name] *= multiplier
                elif style == "compound" and ("-" in last_name or " " in last_name):
                    for name in candidates:
                        candidates[name] *= multiplier
        choice = self.rng.choices(
            list(candidates),
            weights=[float(weight) for weight in candidates.values()],
            k=1,
        )[0]
        return choice, f"{first_source}:distinctive_flair"

    def _maybe_initial_first_name(
        self,
        first_name: str,
        first_source: str,
        *,
        is_international: bool,
        chance: float,
    ) -> tuple[str, str]:
        if chance <= 0:
            return first_name, first_source
        adjusted_chance = chance * (0.45 if is_international else 1.0)
        if self.rng.random() >= adjusted_chance:
            return first_name, first_source
        choice = self.rng.choices(
            [name for name, _weight in INITIAL_FIRST_NAMES],
            weights=[weight for _name, weight in INITIAL_FIRST_NAMES],
            k=1,
        )[0]
        return choice, f"{first_source}:initials"

    def _maybe_family_suffix(
        self,
        last_name: str,
        last_source: str,
        *,
        is_international: bool,
        chance: float,
    ) -> tuple[str, str]:
        if chance <= 0 or any(last_name.endswith(f" {suffix}") for suffix, _weight in FAMILY_SUFFIXES):
            return last_name, last_source
        adjusted_chance = chance * (0.25 if is_international else 1.0)
        if self.rng.random() >= adjusted_chance:
            return last_name, last_source
        suffix = self.rng.choices(
            [suffix for suffix, _weight in FAMILY_SUFFIXES],
            weights=[weight for _suffix, weight in FAMILY_SUFFIXES],
            k=1,
        )[0]
        return f"{last_name} {suffix}", f"{last_source}:family_suffix"

    def _base_rows(self, component_type: str) -> list[dict[str, object]]:
        if component_type == "first":
            return self.first_names
        if component_type == "last":
            return self.last_names
        raise ValueError(f"Unknown component type: {component_type}")

    def _build_component_style_lookup(self) -> dict[tuple[str, str], set[str]]:
        lookup: dict[tuple[str, str], set[str]] = {}
        for (style_key, component_type), rows in self.style_components.items():
            if style_key in {"us_general", "football", "international_misc"}:
                continue
            for row in rows:
                name_key = normalize_name_key(str(row["name"]))
                if not name_key:
                    continue
                lookup.setdefault((component_type, name_key), set()).add(style_key)
        return lookup

    def culture_styles_for_name(
        self,
        *,
        first_name: str,
        last_name: str,
    ) -> set[str]:
        styles: set[str] = set()
        first_key = normalize_name_key(first_name)
        last_key = normalize_name_key(last_name.split()[0].split("-")[0])
        styles.update(self.component_style_lookup.get(("first", first_key), set()))
        styles.update(self.component_style_lookup.get(("last", last_key), set()))
        return styles

    def _cultural_name_multiplier(
        self,
        *,
        component_type: str,
        ethnicity_key: str | None,
        name: str,
    ) -> float:
        if not ethnicity_key:
            return 1.0
        styles = self.component_style_lookup.get((component_type, normalize_name_key(name)), set())
        if not styles:
            return 1.0
        primary = PRIMARY_CULTURAL_STYLE_BY_ETHNICITY.get(ethnicity_key, set())
        if styles & primary:
            return 1.0
        multiplier = 1.0
        for style in styles:
            style_multiplier = (
                INCOMPATIBLE_NAME_STYLE_MULTIPLIERS
                .get(ethnicity_key, {})
                .get(component_type, {})
                .get(style)
            )
            if style_multiplier is not None:
                multiplier = min(multiplier, float(style_multiplier))
        return multiplier

    def _weighted_style(self, style_weights: list[tuple[str, int]]) -> str:
        styles = [style for style, _ in style_weights]
        weights = [
            max(1, int(round(weight * FOOTBALL_STYLE_WEIGHT_MULTIPLIER))) if style == "football" else weight
            for style, weight in style_weights
        ]
        return self.rng.choices(styles, weights=weights, k=1)[0]

    def _choose_component(
        self,
        rows: list[dict[str, object]],
        *,
        football_bias: float,
        component_type: str,
        ethnicity_key: str | None,
    ) -> tuple[str, str]:
        football_rows = [row for row in rows if int(row["football_weight"]) > 0]
        adjusted_football_bias = max(0.0, min(1.0, football_bias * FOOTBALL_DIRECT_BIAS_MULTIPLIER))
        use_football = football_rows and self.rng.random() < adjusted_football_bias
        pool = football_rows if use_football else rows
        weight_key = "football_weight" if use_football else "weight"
        outlier_rate = STYLE_COMPATIBILITY_OUTLIER_RATE.get(component_type, 0.015)
        allow_outlier = bool(ethnicity_key) and self.rng.random() < outlier_rate
        weights = [
            max(
                1,
                int(
                    (
                        int(row[weight_key])
                        * (
                            FOOTBALL_BASE_POOL_WEIGHT_MULTIPLIER
                            if not use_football and int(row["football_weight"]) > 0
                            else 1.0
                        )
                    )
                    * (
                        1.0
                        if allow_outlier
                        else self._cultural_name_multiplier(
                            component_type=component_type,
                            ethnicity_key=ethnicity_key,
                            name=str(row["name"]),
                        )
                    )
                ),
            )
            for row in pool
        ]
        choice = self.rng.choices(pool, weights=weights, k=1)[0]
        source = "football" if use_football else str(choice["source"])
        return str(choice["name"]), source
