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
    blended = public_grade + ((true_grade - public_grade) * confidence_weight(confidence, level))
    noise = perception_noise(
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
    blended = public_ceiling + ((true_ceiling - public_ceiling) * confidence_weight(confidence, level))
    noise = perception_noise(
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
