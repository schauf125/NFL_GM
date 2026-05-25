"""Special-teams flex ratings on the existing 1-10 flex scale.

These grades are intentionally data-only for now. They are meant to describe
where a player can help on special teams without yet changing game outcomes.
"""

from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass
from typing import Any, Mapping


SPECIAL_TEAMS_FLEX_ROLES = ("GUN", "PR", "KR", "ST")
SPECIAL_TEAMS_FLEX_LABELS = {
    "GUN": "Gunner",
    "PR": "Punt Return",
    "KR": "Kick Return",
    "ST": "General ST",
}

RETURN_POSITIONS = {"WR", "RB", "CB", "NB", "FS", "SS", "S"}
GUNNER_POSITIONS = {"WR", "CB", "NB", "FS", "SS", "S", "RB", "TE", "ILB", "LB", "OLB"}
GENERAL_ST_POSITIONS = {
    "RB",
    "FB",
    "WR",
    "TE",
    "ILB",
    "LB",
    "OLB",
    "EDGE",
    "CB",
    "NB",
    "FS",
    "SS",
    "S",
    "OT",
    "OG",
    "C",
    "IDL",
    "DT",
    "DE",
    "NT",
}
LOW_ST_POSITIONS = {"QB", "K", "PK", "P", "LS"}


@dataclass(frozen=True)
class SpecialTeamsFlexGrade:
    role: str
    current: int
    potential: int
    notes: str

    @property
    def label(self) -> str:
        return SPECIAL_TEAMS_FLEX_LABELS.get(self.role, self.role)

    def as_json(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "label": self.label,
            "current": int(self.current),
            "potential": int(self.potential),
            "notes": self.notes,
        }


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _clamp_grade(value: float) -> int:
    return int(round(_clamp(value, 1, 10)))


def _stable_rng(seed_key: str | int | None) -> random.Random:
    digest = hashlib.sha256(str(seed_key or "special-teams-flex").encode("utf-8")).hexdigest()
    return random.Random(int(digest[:16], 16))


def _rating(ratings: Mapping[str, Any] | None, key: str, default: float = 50.0) -> float:
    if not ratings:
        return default
    try:
        return float(ratings.get(key, default) or default)
    except (TypeError, ValueError):
        return default


def _profile_value(profile: Mapping[str, Any] | None, key: str, default: float = 50.0) -> float:
    if not profile:
        return default
    try:
        return float(profile.get(key, default) or default)
    except (TypeError, ValueError):
        return default


def _score_to_grade(score: float, rng: random.Random, *, sigma: float = 0.42) -> int:
    # 56-ish lands near 4, 70-ish near 6, elite 90+ can reach 9/10 rarely.
    grade = (score - 30.0) / 8.0
    return _clamp_grade(grade + rng.gauss(0.0, sigma))


def _potential_from_current(
    current: int,
    ceiling_score: float,
    rng: random.Random,
    *,
    age: int | None = None,
    is_rookie: bool = False,
) -> int:
    base = _score_to_grade(ceiling_score, rng, sigma=0.52)
    if is_rookie:
        base += rng.choice([0, 1, 1, 1, 2, 2, 3])
    elif age is not None and age <= 24:
        base += rng.choice([0, 0, 1, 1, 2])
    elif age is not None and age >= 30:
        base -= rng.choice([0, 0, 1, 1])
    return _clamp_grade(max(current, base))


def _return_score(position: str, ratings: Mapping[str, Any], profile: Mapping[str, Any], role_scores: Mapping[str, Any] | None = None) -> float:
    athletic = (
        _rating(ratings, "speed")
        + _rating(ratings, "acceleration")
        + _rating(ratings, "agility")
        + _rating(ratings, "elusiveness")
    ) / 4.0
    secure = (_rating(ratings, "ball_security") + _rating(ratings, "play_recognition")) / 2.0
    role_bonus = _rating(role_scores, "return_specialist", 50.0) - 50.0
    score = athletic * 0.34 + secure * 0.18 + _profile_value(profile, "return_lane_vision") * 0.38 + role_bonus * 0.10
    if position == "RB":
        score += 2.0
    elif position in {"CB", "NB"}:
        score += 1.0
    elif position not in RETURN_POSITIONS:
        score -= 18.0
    return score


def _gunner_score(position: str, ratings: Mapping[str, Any], profile: Mapping[str, Any]) -> float:
    tackle = (
        _rating(ratings, "open_field_tackle")
        + _rating(ratings, "solo_tackle")
        + _rating(ratings, "pursuit_angle")
    ) / 3.0
    score = (
        _profile_value(profile, "gunner_speed") * 0.42
        + _profile_value(profile, "lane_release") * 0.20
        + _profile_value(profile, "coverage_tackle") * 0.20
        + tackle * 0.18
    )
    if position in {"CB", "NB", "FS", "SS", "S", "WR"}:
        score += 3.0
    elif position in {"ILB", "LB", "OLB", "RB", "TE"}:
        score += 0.5
    elif position not in GUNNER_POSITIONS:
        score -= 18.0
    return score


def _general_st_score(position: str, ratings: Mapping[str, Any], profile: Mapping[str, Any]) -> float:
    mental = (
        _rating(ratings, "play_recognition")
        + _rating(ratings, "discipline")
        + _rating(ratings, "stamina")
        + _rating(ratings, "processing_speed")
    ) / 4.0
    score = (
        _profile_value(profile, "lane_release") * 0.18
        + _profile_value(profile, "block_timing") * 0.19
        + _profile_value(profile, "coverage_tackle") * 0.22
        + _profile_value(profile, "penalty_control") * 0.18
        + mental * 0.23
    )
    if position in {"ILB", "LB", "OLB", "EDGE", "TE", "FB", "FS", "SS", "S", "CB", "NB"}:
        score += 3.0
    elif position in {"OT", "OG", "C", "IDL", "DT", "DE", "NT"}:
        score += 1.5
    elif position in LOW_ST_POSITIONS:
        score -= 20.0
    return score


def _known_return_boost(stats: Mapping[str, Any] | None, *, punt: bool) -> float:
    if not stats:
        return 0.0
    attempts_key = "punt_returns" if punt else "kickoff_returns"
    yards_key = "punt_return_yards" if punt else "kickoff_return_yards"
    attempts = float(stats.get(attempts_key) or 0.0)
    yards = float(stats.get(yards_key) or 0.0)
    if attempts >= 80:
        return 18.0
    if attempts >= 35:
        return 13.0
    if attempts >= 12:
        return 8.0
    if attempts >= 3:
        return 4.0
    if yards >= 300:
        return 5.0
    return 0.0


def special_teams_flex_for_profile(
    *,
    position: str,
    ratings: Mapping[str, Any] | None,
    specialist_profile: Mapping[str, Any] | None,
    role_scores: Mapping[str, Any] | None = None,
    overall: int | float | None = None,
    potential_overall: int | float | None = None,
    age: int | None = None,
    years_exp: int | None = None,
    is_rookie: bool = False,
    stats: Mapping[str, Any] | None = None,
    seed_key: str | int | None = None,
    draft_rank: int | None = None,
    college_tier: str | None = None,
    discovery_profile: str | None = None,
) -> dict[str, SpecialTeamsFlexGrade]:
    """Return applicable special-teams flex grades keyed by role code."""

    position = str(position or "").upper()
    if position in LOW_ST_POSITIONS:
        return {}
    rng = _stable_rng(seed_key or f"{position}:{overall}:{potential_overall}:{age}")
    ratings = ratings or {}
    specialist_profile = specialist_profile or {}
    overall_value = float(overall or _rating(ratings, "overall", 55.0))
    potential_value = float(potential_overall or max(overall_value, _rating(ratings, "potential", overall_value)))
    starter_like = overall_value >= 78 and not is_rookie

    scores = {
        "GUN": _gunner_score(position, ratings, specialist_profile),
        "PR": _return_score(position, ratings, specialist_profile, role_scores) + _known_return_boost(stats, punt=True),
        "KR": _return_score(position, ratings, specialist_profile, role_scores) + _known_return_boost(stats, punt=False),
        "ST": _general_st_score(position, ratings, specialist_profile),
    }
    if position == "RB":
        scores["PR"] -= 2.0
    if position in {"OT", "OG", "C", "IDL", "DT", "DE", "NT"}:
        scores["GUN"] -= 15.0
        scores["PR"] -= 25.0
        scores["KR"] -= 25.0
    if position in {"ILB", "LB", "OLB", "EDGE", "TE", "FB"}:
        scores["PR"] -= 14.0
        scores["KR"] -= 12.0

    grades: dict[str, SpecialTeamsFlexGrade] = {}
    for role, score in scores.items():
        if role in {"PR", "KR"} and position not in RETURN_POSITIONS:
            continue
        if role == "GUN" and position not in GUNNER_POSITIONS:
            continue
        if role == "ST" and position not in GENERAL_ST_POSITIONS:
            continue

        current = _score_to_grade(score, rng)
        known_role = False
        if role == "PR":
            known_role = _known_return_boost(stats, punt=True) >= 4 or _profile_value(specialist_profile, "return_lane_vision") >= 76
        elif role == "KR":
            known_role = _known_return_boost(stats, punt=False) >= 4 or _profile_value(specialist_profile, "return_lane_vision") >= 76
        elif role == "GUN":
            known_role = _profile_value(specialist_profile, "gunner_speed") >= 78 or _profile_value(specialist_profile, "coverage_tackle") >= 78
        elif role == "ST":
            known_role = _profile_value(specialist_profile, "coverage_tackle") >= 78 or _profile_value(specialist_profile, "block_timing") >= 78

        if starter_like and not known_role:
            current = min(current, 4)
        if not known_role and role in {"PR", "KR", "GUN"} and current <= 1:
            continue
        if not known_role and role == "ST" and current <= 2 and not is_rookie:
            continue

        ceiling_score = score + max(0.0, potential_value - overall_value) * 0.16 + rng.gauss(2.0 if is_rookie else 0.6, 2.2)
        potential = _potential_from_current(current, ceiling_score, rng, age=age, is_rookie=is_rookie)
        if not known_role and starter_like:
            potential = max(potential, current)
        if is_rookie:
            current, potential = _apply_rookie_context(
                role=role,
                current=current,
                potential=potential,
                position=position,
                known_role=known_role,
                draft_rank=draft_rank,
                college_tier=college_tier,
                discovery_profile=discovery_profile,
                rng=rng,
            )
        notes = _notes(role, position, known_role, is_rookie, years_exp)
        grades[role] = SpecialTeamsFlexGrade(role=role, current=current, potential=potential, notes=notes)
    return grades


def _apply_rookie_context(
    *,
    role: str,
    current: int,
    potential: int,
    position: str,
    known_role: bool,
    draft_rank: int | None,
    college_tier: str | None,
    discovery_profile: str | None,
    rng: random.Random,
) -> tuple[int, int]:
    """Contextualize rookie ST value by likely draft-day usage.

    Early premium picks are usually protected from core coverage units unless
    the return profile is a real part of the evaluation. Later picks, small
    schools, and off-board finds get more room to win an active-day role via
    teams.
    """

    try:
        rank = int(draft_rank) if draft_rank is not None else None
    except (TypeError, ValueError):
        rank = None
    tier = str(college_tier or "")
    discovery = str(discovery_profile or "")

    if rank is not None and rank <= 32 and role in {"GUN", "ST"}:
        cap = 4 if known_role else 3
        if tier == "Small" and known_role and rng.random() < 0.18:
            cap += 1
        current = min(current, cap)
        potential = min(max(current, potential), cap + (2 if known_role else 1))
    elif rank is not None and rank <= 50 and role in {"GUN", "ST"} and not known_role:
        current = min(current, 4)
        potential = min(max(current, potential), 5)

    if role in {"PR", "KR"} and rank is not None and rank <= 32 and known_role:
        # Dynamic returners can still carry day-one special-teams value.
        current = max(current, min(7, current + 1))
        potential = max(potential, current)

    late_pick = rank is None or rank > 120
    discovery_bonus = discovery in {"undiscovered", "hidden_unlisted", "off_public_board"}
    if late_pick and (tier == "Small" or discovery_bonus):
        if role in {"GUN", "ST"} and position in GUNNER_POSITIONS | GENERAL_ST_POSITIONS:
            if rng.random() < (0.18 if known_role else 0.08):
                current = min(8, current + 1)
                potential = min(10, max(potential, current + rng.choice([0, 1, 1, 2])))
        elif role in {"PR", "KR"} and position in RETURN_POSITIONS and rng.random() < 0.10:
            current = min(8, current + 1)
            potential = min(10, max(potential, current + rng.choice([0, 1, 2])))

    return _clamp_grade(current), _clamp_grade(max(current, potential))


def _notes(role: str, position: str, known_role: bool, is_rookie: bool, years_exp: int | None) -> str:
    label = SPECIAL_TEAMS_FLEX_LABELS.get(role, role)
    if known_role:
        return f"{label} value supported by return/coverage profile."
    if is_rookie:
        return f"Rookie {label.lower()} projection from athletic and specialist profile."
    if years_exp is not None and years_exp <= 2:
        return f"Young-player {label.lower()} projection."
    return f"{label} projection from athletic, tackling, and specialist profile."


def flex_json_for_profile(**kwargs: Any) -> dict[str, dict[str, Any]]:
    return {
        role: grade.as_json()
        for role, grade in special_teams_flex_for_profile(**kwargs).items()
    }
