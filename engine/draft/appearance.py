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
    skin_tone: str
    complexion: str
    face_shape: str
    jawline: str
    brow_profile: str
    nose_profile: str
    smile_profile: str
    media_style: str
    accessory_style: str
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
            f"{self.facial_hair_style.lower()}; "
            f"{self.skin_tone.lower()} skin tone; {self.complexion.lower()}; "
            f"{self.face_shape.lower()}, {self.jawline.lower()}, {self.brow_profile.lower()}, "
            f"{self.nose_profile.lower()}, {self.smile_profile.lower()}; "
            f"{self.media_style.lower()}; {self.accessory_style.lower()}"
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
        name_culture_styles: set[str] | None = None,
        position: str | None = None,
        age: int | None = None,
        birth_country: str | None = None,
        is_international: bool = False,
        college_tier: str | None = None,
        rank: int | None = None,
    ) -> AppearanceTraits:
        primary_label = self._choose_primary_label(
            ethnicity_key,
            ethnicity_label,
            name_culture_styles=name_culture_styles,
        )
        secondary_label = self._choose_secondary_label(ethnicity_key, primary_label)
        eye_color = self._choose_eye_color(
            ethnicity_key,
            primary_label=primary_label,
            secondary_label=secondary_label,
        )
        hair_color = self._choose_hair_color(ethnicity_key, primary_label)
        hairstyle = self._choose_hairstyle(ethnicity_key)
        facial_hair = self._choose_facial_hair(position, age)
        skin_tone = self._choose_skin_tone(ethnicity_key, primary_label)
        complexion = self._choose_complexion(skin_tone)
        face_shape = self._choose_profile_trait("face_shape_weights", ethnicity_key)
        jawline = self._choose_profile_trait("jawline_weights", ethnicity_key)
        brow_profile = self._choose_profile_trait("brow_profile_weights", ethnicity_key)
        nose_profile = self._choose_profile_trait("nose_profile_weights", ethnicity_key)
        smile_profile = self._choose_profile_trait("smile_profile_weights", ethnicity_key)
        media_style = self._choose_media_style(
            is_international=is_international,
            college_tier=college_tier,
            rank=rank,
        )
        accessory_style = self._choose_accessory_style(position, age)
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
            skin_tone=skin_tone,
            complexion=complexion,
            face_shape=face_shape,
            jawline=jawline,
            brow_profile=brow_profile,
            nose_profile=nose_profile,
            smile_profile=smile_profile,
            media_style=media_style,
            accessory_style=accessory_style,
            has_mustache=bool(facial_hair["has_mustache"]),
            has_beard=bool(facial_hair["has_beard"]),
            is_hairstyle_outlier=bool(hairstyle["outlier"]),
            is_facial_hair_outlier=bool(facial_hair["outlier"]),
        )

    def _choose_primary_label(
        self,
        ethnicity_key: str,
        fallback: str,
        *,
        name_culture_styles: set[str] | None = None,
    ) -> str:
        options = self.config["ethnicity_components"].get(ethnicity_key)
        if not options:
            return fallback
        if ethnicity_key == "multiracial" and name_culture_styles:
            options = self._align_multiracial_primary_with_name_styles(
                dict(options),
                name_culture_styles,
            )
        return self._weighted_choice(options)

    @staticmethod
    def _align_multiracial_primary_with_name_styles(
        options: dict[str, float],
        name_culture_styles: set[str],
    ) -> dict[str, float]:
        """Keep multiracial photo/readout coherent with strongly styled names."""
        targets: set[str] = set()
        if name_culture_styles & {"african_american", "caribbean"}:
            targets.add("Black / African American")
        if "west_african" in name_culture_styles:
            targets.add("West African")
            targets.add("Black / African American")
        if "hispanic_latino" in name_culture_styles:
            targets.add("Hispanic / Latino")
        if name_culture_styles & {"polynesian", "hawaiian", "samoan"}:
            targets.add("Native Hawaiian / Pacific Islander")
        if name_culture_styles & {"asian", "south_asian", "filipino"}:
            targets.add("Asian")
        if "native_american" in name_culture_styles:
            targets.add("American Indian / Alaska Native")
        if not targets:
            return options
        adjusted: dict[str, float] = {}
        for label, weight in options.items():
            numeric_weight = float(weight)
            if label in targets:
                numeric_weight *= 3.8
            elif label == "White":
                numeric_weight *= 0.10
            else:
                numeric_weight *= 0.45
            adjusted[label] = max(0.1, numeric_weight)
        return adjusted

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

    def _choose_eye_color(
        self,
        ethnicity_key: str,
        *,
        primary_label: str | None = None,
        secondary_label: str | None = None,
    ) -> str:
        options = dict(self.config["eye_color_weights"].get(
            ethnicity_key,
            self.config["eye_color_weights"]["other_unknown"],
        ))
        labels = {label for label in (primary_label, secondary_label) if label}
        black_labels = {"Black / African American", "West African", "Caribbean", "East African"}
        white_labels = {
            "White",
            "Anglo-American",
            "Germanic European",
            "Irish / Scottish",
            "Southern European",
            "Eastern European",
            "Mediterranean",
        }
        black_present = ethnicity_key == "black_african_american" or bool(labels & black_labels)
        white_present = bool(labels & white_labels)
        if black_present:
            # Keep rare outliers possible, but prevent Black-primary prospects
            # from inheriting the much lighter multiracial/unknown eye table.
            multipliers = {
                "Blue": 0.22 if white_present else 0.08,
                "Gray": 0.20 if white_present else 0.04,
                "Green": 0.55 if white_present else 0.30,
                "Hazel": 0.85 if white_present else 0.75,
                "Amber": 1.05,
                "Brown": 1.18 if white_present else 1.26,
            }
            for color, multiplier in multipliers.items():
                if color in options:
                    options[color] *= multiplier
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

    def _choose_skin_tone(self, ethnicity_key: str, primary_label: str) -> str:
        options = dict(
            self.config["skin_tone_weights"].get(
                ethnicity_key,
                self.config["skin_tone_weights"]["other_unknown"],
            )
        )
        adjustments = self.config.get("skin_tone_label_adjustments", {}).get(
            primary_label,
            {},
        )
        for tone, multiplier in adjustments.items():
            if tone in options:
                options[tone] *= float(multiplier)
        return self._weighted_choice(options)

    def _choose_complexion(self, skin_tone: str) -> str:
        options = dict(self.config["complexion_weights"])
        adjustments = self.config.get("complexion_tone_adjustments", {}).get(skin_tone, {})
        for value, multiplier in adjustments.items():
            if value in options:
                options[value] *= float(multiplier)
        return self._weighted_choice(options)

    def _choose_profile_trait(self, config_key: str, ethnicity_key: str) -> str:
        weights_by_group = self.config[config_key]
        options = weights_by_group.get(
            ethnicity_key,
            weights_by_group.get("other_unknown", {}),
        )
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

    def _choose_media_style(
        self,
        *,
        is_international: bool,
        college_tier: str | None,
        rank: int | None,
    ) -> str:
        weights = dict(self.config["media_style_weights"])
        if is_international:
            for style, multiplier in self.config.get("international_media_adjustments", {}).items():
                if style in weights:
                    weights[style] *= float(multiplier)
        if (college_tier or "").lower() == "small":
            for style, multiplier in self.config.get("small_school_media_adjustments", {}).items():
                if style in weights:
                    weights[style] *= float(multiplier)
        if rank is not None and rank <= 64:
            for style, multiplier in self.config.get("top_prospect_media_adjustments", {}).items():
                if style in weights:
                    weights[style] *= float(multiplier)
        return self._weighted_choice(weights)

    def _choose_accessory_style(self, position: str | None, age: int | None) -> str:
        weights = dict(self.config["accessory_style_weights"])
        position_group = self.config["position_groups"].get((position or "").upper())
        adjustments = self.config.get("accessory_position_adjustments", {}).get(position_group, {})
        for style, multiplier in adjustments.items():
            if style in weights:
                weights[style] *= float(multiplier)
        if age is not None and age <= 21:
            for style, multiplier in self.config.get("accessory_young_adjustments", {}).items():
                if style in weights:
                    weights[style] *= float(multiplier)
        return self._weighted_choice(weights)

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
