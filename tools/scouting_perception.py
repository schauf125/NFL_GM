#!/usr/bin/env python3
"""Deterministic team-specific scouting perception helpers."""

from __future__ import annotations

import random
from typing import Any


CONFIDENCE_WEIGHTS = {
    "Unscouted": 0.0,
    "Low": 0.12,
    "Medium": 0.30,
    "High": 0.62,
    "Very High": 0.82,
}

GRADE_SIGMA_BY_CONFIDENCE = {
    "Unscouted": 6.75,
    "Low": 5.35,
    "Medium": 3.65,
    "High": 1.55,
    "Very High": 0.75,
}

CEILING_SIGMA_BY_CONFIDENCE = {
    "Unscouted": 9.5,
    "Low": 7.75,
    "Medium": 5.25,
    "High": 2.25,
    "Very High": 1.1,
}

EARLY_PUBLIC_BOARD_MAX_RANK = 50
EARLY_PUBLIC_CONFIDENCE_WEIGHT_CAPS = {
    "High": 0.54,
    "Very High": 0.72,
}
EARLY_PUBLIC_GRADE_SIGMA_BONUS = {
    "High": 1.25,
    "Very High": 0.85,
}
EARLY_PUBLIC_CEILING_SIGMA_BONUS = {
    "High": 1.75,
    "Very High": 1.20,
}


def row_get(row: Any, key: str, fallback: Any = None) -> Any:
    try:
        if key in row.keys():
            return row[key]
    except AttributeError:
        if isinstance(row, dict):
            return row.get(key, fallback)
    except Exception:
        pass
    return fallback


def normalized_confidence(value: str | None) -> str:
    text = str(value or "Unscouted").strip()
    return text if text in CONFIDENCE_WEIGHTS else "Unscouted"


def confidence_weight(confidence: str | None, level: int | None) -> float:
    label = normalized_confidence(confidence)
    label_weight = CONFIDENCE_WEIGHTS[label]
    numeric_weight = min(0.86, max(0.0, float(level or 0) / 110.0))
    return max(label_weight, numeric_weight)


def early_public_board_context(row: Any) -> bool:
    try:
        rank = int(row_get(row, "public_board_rank") or row_get(row, "scouting_rank") or 9999)
    except (TypeError, ValueError):
        rank = 9999
    return 1 <= rank <= EARLY_PUBLIC_BOARD_MAX_RANK


def contextual_confidence_weight(confidence: str | None, level: int | None, row: Any) -> float:
    label = normalized_confidence(confidence)
    weight = confidence_weight(confidence, level)
    if early_public_board_context(row) and label in EARLY_PUBLIC_CONFIDENCE_WEIGHT_CAPS:
        weight = min(weight, EARLY_PUBLIC_CONFIDENCE_WEIGHT_CAPS[label])
    return weight


def bounded_gauss(
    *,
    game_id: str,
    draft_year: int,
    team_id: int,
    prospect_id: int,
    key: str,
    sigma: float,
    limit: float,
) -> float:
    rng = random.Random(f"{game_id}:{draft_year}:{team_id}:{prospect_id}:{key}")
    return max(-limit, min(limit, rng.gauss(0.0, sigma)))


def perception_noise(
    *,
    game_id: str,
    draft_year: int,
    team_id: int,
    prospect_id: int,
    confidence: str | None,
    level: int | None,
    key: str,
) -> float:
    label = normalized_confidence(confidence)
    if key == "ceiling":
        sigma = CEILING_SIGMA_BY_CONFIDENCE[label]
        limit = max(4.0, sigma * 2.6)
    else:
        sigma = GRADE_SIGMA_BY_CONFIDENCE[label]
        limit = max(3.0, sigma * 2.5)
    # A little extra scouting level should tighten the miss inside a confidence tier.
    level_tightener = max(0.65, 1.0 - (max(0, int(level or 0)) / 220.0))
    return bounded_gauss(
        game_id=game_id,
        draft_year=draft_year,
        team_id=team_id,
        prospect_id=prospect_id,
        key=key,
        sigma=sigma * level_tightener,
        limit=limit,
    )


def contextual_perception_noise(
    row: Any,
    *,
    game_id: str,
    draft_year: int,
    team_id: int,
    prospect_id: int,
    confidence: str | None,
    level: int | None,
    key: str,
) -> float:
    label = normalized_confidence(confidence)
    if key == "ceiling":
        sigma_bonus = EARLY_PUBLIC_CEILING_SIGMA_BONUS.get(label, 0.0)
    else:
        sigma_bonus = EARLY_PUBLIC_GRADE_SIGMA_BONUS.get(label, 0.0)
    if not early_public_board_context(row) or sigma_bonus <= 0:
        return perception_noise(
            game_id=game_id,
            draft_year=draft_year,
            team_id=team_id,
            prospect_id=prospect_id,
            confidence=confidence,
            level=level,
            key=key,
        )
    level_tightener = max(0.72, 1.0 - (max(0, int(level or 0)) / 260.0))
    base_sigma = CEILING_SIGMA_BY_CONFIDENCE[label] if key == "ceiling" else GRADE_SIGMA_BY_CONFIDENCE[label]
    sigma = (base_sigma + sigma_bonus) * level_tightener
    limit = max(4.5 if key == "ceiling" else 3.5, sigma * 2.65)
    return bounded_gauss(
        game_id=game_id,
        draft_year=draft_year,
        team_id=team_id,
        prospect_id=prospect_id,
        key=f"early-public-{key}",
        sigma=sigma,
        limit=limit,
    )


def perceived_grade(row: Any, *, game_id: str, draft_year: int, team_id: int) -> float:
    public_grade = float(
        row_get(row, "scout_grade")
        or row_get(row, "overall")
        or row_get(row, "true_grade")
        or 50
    )
    true_grade = float(row_get(row, "true_grade") or public_grade)
    confidence = row_get(row, "cpu_scouting_confidence") or row_get(row, "scouting_confidence")
    level = int(row_get(row, "cpu_scouting_level") or row_get(row, "scouting_level") or 0)
    blended = public_grade + ((true_grade - public_grade) * contextual_confidence_weight(confidence, level, row))
    noise = contextual_perception_noise(
        row,
        game_id=game_id,
        draft_year=draft_year,
        team_id=team_id,
        prospect_id=int(row_get(row, "prospect_id")),
        confidence=confidence,
        level=level,
        key="grade",
    )
    return max(20.0, min(99.0, blended + noise))


def perceived_ceiling(row: Any, *, game_id: str, draft_year: int, team_id: int) -> float:
    public_ceiling = float(
        row_get(row, "scout_ceiling")
        or row_get(row, "potential")
        or row_get(row, "ceiling_grade")
        or perceived_grade(row, game_id=game_id, draft_year=draft_year, team_id=team_id)
    )
    true_ceiling = float(row_get(row, "ceiling_grade") or public_ceiling)
    confidence = row_get(row, "cpu_scouting_confidence") or row_get(row, "scouting_confidence")
    level = int(row_get(row, "cpu_scouting_level") or row_get(row, "scouting_level") or 0)
    blended = public_ceiling + ((true_ceiling - public_ceiling) * contextual_confidence_weight(confidence, level, row))
    noise = contextual_perception_noise(
        row,
        game_id=game_id,
        draft_year=draft_year,
        team_id=team_id,
        prospect_id=int(row_get(row, "prospect_id")),
        confidence=confidence,
        level=level,
        key="ceiling",
    )
    grade_floor = perceived_grade(row, game_id=game_id, draft_year=draft_year, team_id=team_id)
    return max(grade_floor, min(99.0, blended + noise))
