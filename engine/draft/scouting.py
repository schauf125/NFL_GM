"""Modular scouting reports over true draft-prospect ratings.

Scouting reports are intentionally a lens over the true player model. The
underlying ratings stay stable, while the report can be noisy, biased, or
incomplete depending on the scout profile. Later game systems can swap lenses by
team, scout, event, visit, or scouting budget without rewriting report text.
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from .attributes import DISPLAY_NAMES, ROLE_WEIGHTS, DraftProspectAttributes, clamp, rank_tier


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SCOUTING_DIR = ROOT / "data" / "draft" / "scouting"
DEFAULT_LENS_WEIGHTS = {
    "default": 34,
    "national_scout": 16,
    "optimistic_area": 10,
    "conservative_crosscheck": 10,
    "traits_scout": 8,
    "analytics_scout": 8,
    "old_school_scout": 6,
    "medical_crosscheck": 4,
    "small_school_scout": 4,
}

ROLE_LABELS = {
    "pocket_qb": "pocket quarterback",
    "scrambling_qb": "scrambling quarterback",
    "power_rb": "power back",
    "elusive_rb": "space back",
    "boundary_wr": "boundary receiver",
    "slot_wr": "slot receiver",
    "inline_te": "inline tight end",
    "move_te": "move tight end",
    "pass_protecting_ot": "pass-protecting tackle",
    "interior_run_blocker": "interior run blocker",
    "speed_edge": "speed edge rusher",
    "power_edge": "power edge defender",
    "interior_rusher": "interior rusher",
    "nose_run_stopping_dt": "nose tackle",
    "coverage_lb": "coverage linebacker",
    "box_lb": "box linebacker",
    "man_cb": "man corner",
    "zone_cb": "zone corner",
    "deep_safety": "deep safety",
    "box_safety": "box safety",
}


@dataclass(frozen=True)
class ScoutingLens:
    key: str
    label: str
    accuracy: float
    grade_sigma: float
    rating_sigma: float
    optimism: float
    risk_blindness: float
    variance_note_chance: float


@dataclass(frozen=True)
class ScoutingReport:
    lens_key: str
    scout_label: str
    scout_confidence: str
    perceived_grade: int
    perceived_ceiling: int
    perceived_risk: str
    summary: str
    strengths: list[str]
    concerns: list[str]
    projection: str
    role_fit: str
    usage_note: str
    development_note: str
    risk_note: str
    lens_note: str
    variance_note: str

    @property
    def strengths_text(self) -> str:
        return "; ".join(self.strengths)

    @property
    def concerns_text(self) -> str:
        return "; ".join(self.concerns)

    @property
    def full_text(self) -> str:
        pieces = [
            self.summary,
            f"Strengths: {self.strengths_text}.",
            f"Concerns: {self.concerns_text}.",
            self.projection,
            self.role_fit,
            self.usage_note,
            self.development_note,
            self.risk_note,
            self.lens_note,
        ]
        if self.variance_note:
            pieces.append(self.variance_note)
        return " ".join(piece for piece in pieces if piece)


class ScoutingReportGenerator:
    """Generate opinionated scouting reports from normalized prospect profiles."""

    def __init__(
        self,
        *,
        seed: str | int | None = None,
        scouting_dir: Path = DEFAULT_SCOUTING_DIR,
        lens_key: str = "default",
    ) -> None:
        self.seed = seed
        self.rng = random.Random(seed)
        self.scouting_dir = scouting_dir
        self.rating_phrases = _load_json(scouting_dir / "rating_phrases.json")
        self.report_templates = _load_json(scouting_dir / "report_templates.json")
        self.lenses = {
            key: _lens_from_config(key, value)
            for key, value in _load_json(scouting_dir / "scout_lenses.json").items()
        }
        if lens_key not in self.lenses:
            raise ValueError(f"Unknown scouting lens: {lens_key}")
        self.lens_key = lens_key

    def choose_lens_key(
        self,
        rank: int,
        position: str | None = None,
        *,
        discovery_profile: str = "public_board",
        college_tier: str | None = None,
    ) -> str:
        """Pick a scout lens for preview output.

        High-profile players get a little more national/cross-check attention.
        Back-end players get slightly more area/traits-scout variance.
        """

        weights = dict(DEFAULT_LENS_WEIGHTS)
        if discovery_profile == "hidden_unlisted":
            weights["national_scout"] = max(1, weights["national_scout"] - 12)
            weights["default"] = max(4, weights["default"] - 10)
            weights["optimistic_area"] += 12
            weights["traits_scout"] += 10
            weights["small_school_scout"] += 20
            weights["old_school_scout"] += 4
        elif rank <= 32:
            weights["national_scout"] += 8
            weights["conservative_crosscheck"] += 4
            weights["analytics_scout"] += 3
            weights["old_school_scout"] += 2
        elif rank > 256:
            weights["optimistic_area"] += 6
            weights["traits_scout"] += 5
            weights["small_school_scout"] += 8
        if college_tier == "Small":
            weights["small_school_scout"] += 8
            weights["optimistic_area"] += 3
            weights["national_scout"] = max(1, weights["national_scout"] - 4)
        elif college_tier == "Regular":
            weights["small_school_scout"] += 2
        if position in {"QB", "OT", "EDGE", "CB", "WR"}:
            weights["national_scout"] += 2
            weights["analytics_scout"] += 1
        elif position in {"K", "P", "LS"}:
            weights["analytics_scout"] += 4
            weights["conservative_crosscheck"] += 2
        keys = [key for key in weights if key in self.lenses]
        values = [weights[key] for key in keys]
        return self.rng.choices(keys, weights=values, k=1)[0]

    def generate(
        self,
        *,
        name: str,
        position: str,
        rank: int,
        attributes: DraftProspectAttributes,
        lens_key: str | None = None,
        discovery_profile: str = "public_board",
        college_tier: str | None = None,
    ) -> ScoutingReport:
        lens = self.lenses[lens_key or self.lens_key]
        lens = self._contextual_lens(
            lens,
            rank=rank,
            discovery_profile=discovery_profile,
            college_tier=college_tier,
        )
        perceived_grade = self._perceived_grade(attributes.true_grade, lens)
        perceived_ceiling = self._perceived_ceiling(
            attributes.ceiling_grade,
            perceived_grade,
            lens,
        )
        perceived_ratings = self._perceived_ratings(attributes.ratings, lens)
        relevant_keys = self._relevant_rating_keys(position, attributes)
        strengths = self._phrases(
            perceived_ratings,
            relevant_keys,
            phrase_type="strength",
            count=3,
            highest=True,
        )
        concerns = self._phrases(
            perceived_ratings,
            relevant_keys,
            phrase_type="concern",
            count=2,
            highest=False,
        )
        perceived_risk = self._perceived_risk(
            attributes.risk_level,
            perceived_grade,
            perceived_ceiling,
            lens,
        )
        summary = self._summary(
            name=name,
            position=position,
            attributes=attributes,
            perceived_grade=perceived_grade,
        )
        projection = self._projection(rank)
        role_fit = self._role_fit(attributes.primary_role)
        usage_note = self._usage_note(position, attributes.primary_role)
        development_note = self._development_note(
            rank=rank,
            perceived_grade=perceived_grade,
            perceived_risk=perceived_risk,
        )
        risk_note = self._risk_note(perceived_risk)
        lens_note = self._lens_note(lens)
        variance_note = self._variance_note(lens)
        confidence = self._confidence(lens)
        return ScoutingReport(
            lens_key=lens.key,
            scout_label=lens.label,
            scout_confidence=confidence,
            perceived_grade=perceived_grade,
            perceived_ceiling=perceived_ceiling,
            perceived_risk=perceived_risk,
            summary=summary,
            strengths=strengths,
            concerns=concerns,
            projection=projection,
            role_fit=role_fit,
            usage_note=usage_note,
            development_note=development_note,
            risk_note=risk_note,
            lens_note=lens_note,
            variance_note=variance_note,
        )

    def _perceived_grade(self, true_grade: int, lens: ScoutingLens) -> int:
        sigma = lens.grade_sigma * (1.25 - min(0.95, lens.accuracy) * 0.55)
        return clamp(true_grade + lens.optimism + self.rng.gauss(0, sigma), 25, 90)

    def _perceived_ceiling(
        self,
        true_ceiling: int,
        perceived_grade: int,
        lens: ScoutingLens,
    ) -> int:
        sigma = (lens.grade_sigma + 1.5) * (1.25 - min(0.95, lens.accuracy) * 0.55)
        ceiling = true_ceiling + (lens.optimism * 1.4) + self.rng.gauss(0, sigma)
        return clamp(ceiling, perceived_grade, 94)

    def _perceived_ratings(
        self,
        ratings: dict[str, int],
        lens: ScoutingLens,
    ) -> dict[str, int]:
        sigma = lens.rating_sigma * (1.25 - min(0.95, lens.accuracy) * 0.55)
        return {
            key: clamp(value + lens.optimism + self.rng.gauss(0, sigma), 1, 99)
            for key, value in ratings.items()
        }

    def _relevant_rating_keys(
        self,
        position: str,
        attributes: DraftProspectAttributes,
    ) -> list[str]:
        keys: set[str] = set()
        for role in (attributes.primary_role, attributes.secondary_role):
            keys.update(ROLE_WEIGHTS.get(role, {}))
        if not keys:
            if position in {"K", "P"}:
                keys = {"kick_power", "kick_accuracy", "composure", "discipline", "durability"}
            elif position == "LS":
                keys = {
                    "block_sustain",
                    "pass_block_power",
                    "pass_block_speed",
                    "tackle_wrap",
                    "durability",
                    "stamina",
                    "discipline",
                }
            else:
                keys = {"speed", "acceleration", "agility", "strength", "composure", "durability"}
        return sorted(keys)

    def _phrases(
        self,
        perceived_ratings: dict[str, int],
        relevant_keys: list[str],
        *,
        phrase_type: str,
        count: int,
        highest: bool,
    ) -> list[str]:
        ordered = [
            (key, perceived_ratings[key])
            for key in relevant_keys
            if key in perceived_ratings
        ]
        ordered.sort(key=lambda item: item[1], reverse=highest)
        phrases: list[str] = []
        for key, _value in ordered:
            phrase_options = self.rating_phrases.get(key, {}).get(phrase_type)
            if not phrase_options:
                display = DISPLAY_NAMES.get(key, key.replace("_", " ").title())
                phrase_options = [f"{display.lower()} is a reportable trait"]
            phrase = self.rng.choice(phrase_options)
            if phrase not in phrases:
                phrases.append(phrase)
            if len(phrases) >= count:
                return phrases
        return phrases

    def _summary(
        self,
        *,
        name: str,
        position: str,
        attributes: DraftProspectAttributes,
        perceived_grade: int,
    ) -> str:
        if perceived_grade >= 72:
            bucket = "high_end"
        elif perceived_grade >= 62:
            bucket = "starter_path"
        elif perceived_grade >= 52:
            bucket = "developmental"
        else:
            bucket = "roster_bubble"
        opener = self.rng.choice(self.report_templates["summary_openers"][bucket])
        archetype_text = attributes.archetype.lower()
        role_text = self._role_label(attributes.primary_role) or self._position_label(position)
        if archetype_text == role_text:
            fit_text = f"and the scout sees the same role as the cleanest early fit."
        else:
            fit_text = f"with {_article(role_text)} {role_text} projection."
        return (
            opener.format(name=name)
            + f" The current report frames him as {_article(archetype_text)} {archetype_text} "
            + fit_text
        )

    def _projection(self, rank: int) -> str:
        tier = rank_tier(rank)
        return self.rng.choice(self.report_templates["projection"][tier])

    def _role_fit(self, primary_role: str) -> str:
        if not primary_role:
            return ""
        role_label = self._role_label(primary_role)
        return self.rng.choice(self.report_templates["role_fit"]).format(role=role_label)

    def _usage_note(self, position: str, primary_role: str) -> str:
        usage_templates = self.report_templates.get("usage_notes", {})
        options = usage_templates.get(primary_role)
        if not options:
            options = usage_templates.get(position.upper())
        if not options:
            options = usage_templates.get("default")
        if not options:
            return ""
        role_label = self._role_label(primary_role) if primary_role else self._position_label(position)
        return self.rng.choice(options).format(role=role_label, position=self._position_label(position))

    def _development_note(
        self,
        *,
        rank: int,
        perceived_grade: int,
        perceived_risk: str,
    ) -> str:
        if perceived_risk == "High":
            bucket = "high_risk"
        elif perceived_grade >= 72:
            bucket = "high_end"
        elif perceived_grade >= 62:
            bucket = "starter_path"
        elif rank_tier(rank) in {"round_6_7", "leftover"}:
            bucket = "roster_bubble"
        else:
            bucket = "developmental"
        options = self.report_templates.get("development_notes", {}).get(bucket)
        if not options:
            return ""
        return self.rng.choice(options)

    def _lens_note(self, lens: ScoutingLens) -> str:
        options = self.report_templates.get("lens_notes", {}).get(lens.key)
        if not options:
            return ""
        return self.rng.choice(options)

    @staticmethod
    def _role_label(primary_role: str) -> str:
        return ROLE_LABELS.get(primary_role, primary_role.replace("_", " "))

    @staticmethod
    def _position_label(position: str) -> str:
        return {
            "K": "kicker",
            "P": "punter",
            "LS": "long snapper",
        }.get(position.upper(), position.lower())

    def _risk_note(self, perceived_risk: str) -> str:
        options = self.report_templates.get("risk_notes", {}).get(perceived_risk)
        if not options:
            return ""
        return self.rng.choice(options)

    def _variance_note(self, lens: ScoutingLens) -> str:
        if self.rng.random() > lens.variance_note_chance:
            return ""
        return self.rng.choice(self.report_templates["variance_notes"])

    def _perceived_risk(
        self,
        true_risk: str,
        perceived_grade: int,
        perceived_ceiling: int,
        lens: ScoutingLens,
    ) -> str:
        risk_order = ["Low", "Medium", "High"]
        risk_index = risk_order.index(true_risk) if true_risk in risk_order else 1
        gap = perceived_ceiling - perceived_grade
        if gap >= 18:
            risk_index += 1
        elif gap <= 8 and perceived_grade >= 60:
            risk_index -= 1
        if perceived_grade <= 46 and gap >= 10:
            risk_index += 1
        elif perceived_grade <= 54 and risk_index == 0:
            risk_index = 1
        if self.rng.random() < abs(lens.risk_blindness):
            risk_index += -1 if lens.risk_blindness > 0 else 1
        return risk_order[max(0, min(2, risk_index))]

    @staticmethod
    def _confidence(lens: ScoutingLens) -> str:
        if lens.accuracy >= 0.76:
            return "High"
        if lens.accuracy >= 0.62:
            return "Medium"
        return "Low"

    def _contextual_lens(
        self,
        lens: ScoutingLens,
        *,
        rank: int,
        discovery_profile: str,
        college_tier: str | None,
    ) -> ScoutingLens:
        accuracy = lens.accuracy
        grade_sigma = lens.grade_sigma
        rating_sigma = lens.rating_sigma
        variance_note_chance = lens.variance_note_chance

        if discovery_profile == "hidden_unlisted":
            accuracy -= 0.22
            grade_sigma += 5.8
            rating_sigma += 8.0
            variance_note_chance += 0.35
        elif rank > 256:
            accuracy -= 0.06
            grade_sigma += 1.8
            rating_sigma += 2.2
            variance_note_chance += 0.08

        if college_tier == "Small":
            accuracy -= 0.08
            grade_sigma += 2.3
            rating_sigma += 2.8
            variance_note_chance += 0.12
        elif college_tier == "Regular":
            accuracy -= 0.03
            grade_sigma += 0.9
            rating_sigma += 1.1

        return replace(
            lens,
            accuracy=max(0.25, min(0.95, accuracy)),
            grade_sigma=max(1.0, grade_sigma),
            rating_sigma=max(1.0, rating_sigma),
            variance_note_chance=max(0.0, min(0.92, variance_note_chance)),
        )


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(path)
    return json.loads(path.read_text(encoding="utf-8"))


def _lens_from_config(key: str, config: dict[str, Any]) -> ScoutingLens:
    return ScoutingLens(
        key=key,
        label=str(config.get("label", key)),
        accuracy=float(config.get("accuracy", 0.65)),
        grade_sigma=float(config.get("grade_sigma", 5.0)),
        rating_sigma=float(config.get("rating_sigma", 7.0)),
        optimism=float(config.get("optimism", 0.0)),
        risk_blindness=float(config.get("risk_blindness", 0.0)),
        variance_note_chance=float(config.get("variance_note_chance", 0.15)),
    )


def _article(text: str) -> str:
    return "an" if text[:1].lower() in {"a", "e", "i", "o", "u"} else "a"
