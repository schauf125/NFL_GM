"""Appearance metadata generation for fictional draft prospects."""

from __future__ import annotations

import json
import random
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_APPEARANCE_CONFIG = ROOT / "data" / "draft" / "appearance" / "appearance_traits.json"


@dataclass(frozen=True)
class AppearanceTraits:
    primary_ethnicity_key: str
    primary_ethnicity_label: str
    secondary_ethnicity_label: str | None
    ethnicity_note: str
    eye_color: str
    hair_color: str
    hairstyle: str
    facial_hair_style: str
    has_mustache: bool
    has_beard: bool
    is_hairstyle_outlier: bool
    is_facial_hair_outlier: bool

    @property
    def photo_ethnicity(self) -> str:
        if self.secondary_ethnicity_label:
            return f"{self.primary_ethnicity_label} + {self.secondary_ethnicity_label}"
        return self.primary_ethnicity_label

    @property
    def photo_prompt_traits(self) -> str:
        return (
            f"{self.photo_ethnicity}; {self.eye_color.lower()} eyes; "
            f"hair: {self.hair_color.lower()} {self.hairstyle.lower()}; "
            f"{self.facial_hair_style.lower()}"
        )


class AppearanceGenerator:
    """Generate eye color, hairstyle, facial hair, and photo ethnicity metadata."""

    def __init__(
        self,
        config_path: Path = DEFAULT_APPEARANCE_CONFIG,
        *,
        seed: str | int | None = None,
    ) -> None:
        if not config_path.exists():
            raise FileNotFoundError(f"Appearance config not found: {config_path}")
        self.config_path = config_path
        self.config = json.loads(config_path.read_text(encoding="utf-8"))
        self.rng = random.Random(seed)

    def generate(
        self,
        *,
        ethnicity_key: str,
        ethnicity_label: str,
        position: str | None = None,
        age: int | None = None,
    ) -> AppearanceTraits:
        primary_label = self._choose_primary_label(ethnicity_key, ethnicity_label)
        secondary_label = self._choose_secondary_label(ethnicity_key, primary_label)
        eye_color = self._choose_eye_color(ethnicity_key)
        hair_color = self._choose_hair_color(ethnicity_key, primary_label)
        hairstyle = self._choose_hairstyle(ethnicity_key)
        facial_hair = self._choose_facial_hair(position, age)
        ethnicity_note = primary_label
        if secondary_label:
            ethnicity_note = f"{primary_label} + {secondary_label}"
        return AppearanceTraits(
            primary_ethnicity_key=ethnicity_key,
            primary_ethnicity_label=primary_label,
            secondary_ethnicity_label=secondary_label,
            ethnicity_note=ethnicity_note,
            eye_color=eye_color,
            hair_color=hair_color,
            hairstyle=hairstyle["style"],
            facial_hair_style=facial_hair["style"],
            has_mustache=bool(facial_hair["has_mustache"]),
            has_beard=bool(facial_hair["has_beard"]),
            is_hairstyle_outlier=bool(hairstyle["outlier"]),
            is_facial_hair_outlier=bool(facial_hair["outlier"]),
        )

    def _choose_primary_label(self, ethnicity_key: str, fallback: str) -> str:
        options = self.config["ethnicity_components"].get(ethnicity_key)
        if not options:
            return fallback
        return self._weighted_choice(options)

    def _choose_secondary_label(
        self,
        ethnicity_key: str,
        primary_label: str,
    ) -> str | None:
        chance = float(self.config["secondary_ethnicity_chance"])
        if ethnicity_key == "multiracial":
            chance = float(self.config["multiracial_secondary_chance"])
        if self.rng.random() >= chance:
            return None
        options = dict(self.config["secondary_pair_weights"].get(ethnicity_key, {}))
        options.pop(primary_label, None)
        if not options:
            return None
        return self._weighted_choice(options)

    def _choose_eye_color(self, ethnicity_key: str) -> str:
        options = self.config["eye_color_weights"].get(
            ethnicity_key,
            self.config["eye_color_weights"]["other_unknown"],
        )
        return self._weighted_choice(options)

    def _choose_hair_color(self, ethnicity_key: str, primary_label: str) -> str:
        options = dict(
            self.config["hair_color_weights"].get(
                ethnicity_key,
                self.config["hair_color_weights"]["other_unknown"],
            )
        )
        adjustments = self.config.get("hair_color_label_adjustments", {}).get(
            primary_label,
            {},
        )
        for color, multiplier in adjustments.items():
            if color in options:
                options[color] *= float(multiplier)
        return self._weighted_choice(options)

    def _choose_hairstyle(self, ethnicity_key: str) -> dict[str, object]:
        if self.rng.random() < float(self.config["hairstyle_outlier_chance"]):
            return {
                "style": self._weighted_choice(self.config["hairstyle_outlier_weights"]),
                "outlier": True,
            }
        options = self.config["hairstyle_weights"].get(
            ethnicity_key,
            self.config["hairstyle_weights"]["other_unknown"],
        )
        return {"style": self._weighted_choice(options), "outlier": False}

    def _choose_facial_hair(
        self,
        position: str | None,
        age: int | None,
    ) -> dict[str, object]:
        styles = self.config["facial_hair_styles"]
        weights = {
            style: float(data["weight"])
            for style, data in styles.items()
        }
        position_group = self.config["position_groups"].get((position or "").upper())
        adjustments = self.config["position_style_adjustments"].get(position_group, {})
        for style, multiplier in adjustments.items():
            if style in weights:
                weights[style] *= float(multiplier)
        age_adjustments = self.config.get("age_style_adjustments", {}).get(
            self._age_group(age),
            {},
        )
        for style, multiplier in age_adjustments.items():
            if style in weights:
                weights[style] *= float(multiplier)
        chosen_style = self._weighted_choice(weights)
        data = styles[chosen_style]
        return {
            "style": chosen_style,
            "has_mustache": data["has_mustache"],
            "has_beard": data["has_beard"],
            "outlier": data["outlier"],
        }

    @staticmethod
    def _age_group(age: int | None) -> str:
        if age is None:
            return "unknown"
        if age <= 21:
            return "20_21"
        if age == 22:
            return "22"
        if age == 23:
            return "23"
        return "24_plus"

    def _weighted_choice(self, weights: dict[str, float]) -> str:
        names = list(weights)
        values = [float(weights[name]) for name in names]
        return self.rng.choices(names, weights=values, k=1)[0]
