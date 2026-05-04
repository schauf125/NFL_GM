"""College-class and Senior Bowl helpers for generated draft prospects."""

from __future__ import annotations

import random
from dataclasses import dataclass


@dataclass(frozen=True)
class SeniorBowlStatus:
    college_class: str
    eligible: bool
    invited: bool
    accepted: bool
    result: str
    notes: str


def _rng(*parts: object) -> random.Random:
    return random.Random("|".join(str(part) for part in parts))


def _weighted_choice(rng: random.Random, choices: list[tuple[str, float]]) -> str:
    roll = rng.random() * sum(weight for _, weight in choices)
    running = 0.0
    for label, weight in choices:
        running += weight
        if roll <= running:
            return label
    return choices[-1][0]


def college_class_for_age(age: int | None, prospect_key: object | None = None) -> str:
    """Return a mostly age-correlated college class label.

    These are scouting-game labels rather than exact NCAA eligibility records.
    The small deterministic variance keeps every class from being a perfect
    age lookup while still making ages feel right.
    """

    try:
        normalized_age = int(age) if age is not None else 22
    except (TypeError, ValueError):
        normalized_age = 22
    rng = _rng("college-class", prospect_key or "prospect", normalized_age)
    if normalized_age <= 20:
        choices = [("Sophomore", 0.72), ("Junior", 0.26), ("Senior", 0.02)]
    elif normalized_age == 21:
        choices = [("Sophomore", 0.08), ("Junior", 0.70), ("Senior", 0.22)]
    elif normalized_age == 22:
        choices = [("Junior", 0.24), ("Senior", 0.66), ("Graduated", 0.10)]
    elif normalized_age == 23:
        choices = [("Junior", 0.04), ("Senior", 0.56), ("Graduated", 0.40)]
    else:
        choices = [("Senior", 0.18), ("Graduated", 0.82)]
    return _weighted_choice(rng, choices)


def senior_bowl_status(
    *,
    age: int | None,
    prospect_key: object | None = None,
    college_class: str | None = None,
    public_board_rank: int | None = None,
    public_board_status: str | None = None,
    projected_round: int | None = None,
    college_tier: str | None = None,
    position: str | None = None,
    combine_injured: bool = False,
    combine_top_skip: bool = False,
    scout_grade: int | None = None,
) -> SeniorBowlStatus:
    class_label = college_class or college_class_for_age(age, prospect_key)
    eligible = class_label in {"Senior", "Graduated"}
    if not eligible:
        return SeniorBowlStatus(
            college_class=class_label,
            eligible=False,
            invited=False,
            accepted=False,
            result="ineligible",
            notes=f"{class_label} prospect; not Senior Bowl eligible yet.",
        )

    tier = str(college_tier or "").lower()
    pos = str(position or "").upper()
    off_public_board = str(public_board_status or "").lower() == "off_public_board"
    rng = _rng(
        "senior-bowl",
        prospect_key or "prospect",
        age,
        class_label,
        public_board_rank,
        public_board_status,
        projected_round,
    )
    if off_public_board:
        invite_chance = 0.07 if class_label == "Senior" else 0.10
    else:
        invite_chance = 0.45 if class_label == "Senior" else 0.55
    if public_board_rank is not None:
        rank = int(public_board_rank)
        if rank <= 75:
            invite_chance += 0.25
        elif rank <= 150:
            invite_chance += 0.15
        elif rank <= 250:
            invite_chance += 0.08
    else:
        invite_chance += 0.04
    if projected_round is not None:
        round_number = int(projected_round)
        if round_number <= 3:
            invite_chance += 0.16
        elif round_number <= 5:
            invite_chance += 0.08
    if "small" in tier or "fcs" in tier or "group" in tier:
        invite_chance += 0.04 if off_public_board else 0.08
    elif "power" in tier:
        invite_chance += 0.02
    if pos in {"K", "P", "LS"}:
        invite_chance -= 0.10
    if combine_injured:
        invite_chance -= 0.14
    if combine_top_skip:
        invite_chance -= 0.12
    if scout_grade is not None:
        grade = int(scout_grade)
        if grade >= 72:
            invite_chance += 0.12 if off_public_board else 0.08
        elif grade >= 64 and off_public_board:
            invite_chance += 0.06
        elif grade <= 55:
            invite_chance -= 0.08
    if off_public_board:
        invite_chance += rng.gauss(0.0, 0.025)
        invite_chance = max(0.02, min(0.28, invite_chance))
    else:
        invite_chance = max(0.15, min(0.88, invite_chance))
    invited = rng.random() < invite_chance
    if not invited:
        return SeniorBowlStatus(
            college_class=class_label,
            eligible=True,
            invited=False,
            accepted=False,
            result="not_invited",
            notes="Eligible but not selected for the Senior Bowl invite list.",
        )

    accept_chance = 0.76
    if class_label == "Graduated":
        accept_chance += 0.05
    if public_board_rank is not None and int(public_board_rank) <= 32:
        accept_chance -= 0.35
    if projected_round is not None and int(projected_round) == 1:
        accept_chance -= 0.18
    if "small" in tier or "fcs" in tier or "group" in tier:
        accept_chance += 0.10
    if combine_injured:
        accept_chance -= 0.30
    if combine_top_skip:
        accept_chance -= 0.20
    accept_chance = max(0.18, min(0.92, accept_chance))
    accepted = rng.random() < accept_chance
    if accepted:
        notes = "Accepted Senior Bowl invitation; teams can gain extra live-practice scouting exposure."
        result = "accepted"
    else:
        notes = "Declined or skipped the Senior Bowl after receiving an invitation."
        result = "declined"
    return SeniorBowlStatus(
        college_class=class_label,
        eligible=True,
        invited=True,
        accepted=accepted,
        result=result,
        notes=notes,
    )
