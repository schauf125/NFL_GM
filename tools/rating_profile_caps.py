"""Football-shaped caps for physical ratings.

These caps are not meant to flatten outliers. They keep common generated/imported
profiles realistic while leaving room for rare exceptions via manual overrides.
"""

from __future__ import annotations


PHYSICAL_KEYS = {"speed", "acceleration", "agility", "strength"}

BIG_WR_SPEED_EXCEPTIONS = {
    "DK Metcalf",
}


def clamp_int(value: float, low: int = 1, high: int = 100) -> int:
    return max(low, min(high, int(round(value))))


def age_cap(age: int | None, bands: list[tuple[int, tuple[int, int, int]]]) -> tuple[int, int, int] | None:
    if age is None:
        return None
    for minimum_age, caps in bands:
        if age >= minimum_age:
            return caps
    return None


def receiver_size_caps(name: str, height_in: int | None, weight_lbs: int | None, overall: int | None, potential: int | None) -> tuple[int, int, int] | None:
    height = height_in or 0
    weight = weight_lbs or 0
    if height < 74 or weight < 210:
        return None

    speed_cap, acceleration_cap, agility_cap = 87, 88, 86
    if weight >= 220:
        agility_cap -= 1
    if weight >= 225:
        speed_cap -= 1
        acceleration_cap -= 1

    if name in BIG_WR_SPEED_EXCEPTIONS:
        speed_cap += 3
        acceleration_cap += 2
        agility_cap += 1
    return speed_cap, acceleration_cap, agility_cap


def veteran_skill_caps(position: str, age: int | None) -> tuple[int, int, int] | None:
    pos = position.upper()
    if pos == "RB":
        return age_cap(
            age,
            [
                (33, (83, 84, 82)),
                (32, (85, 86, 84)),
                (31, (87, 88, 86)),
                (30, (89, 90, 88)),
                (29, (91, 92, 90)),
            ],
        )
    if pos == "WR":
        return age_cap(
            age,
            [
                (34, (82, 83, 81)),
                (33, (84, 85, 82)),
                (32, (86, 87, 84)),
                (31, (88, 89, 85)),
                (30, (90, 91, 87)),
                (29, (90, 91, 88)),
            ],
        )
    return None


def apply_caps_to_ratings(
    ratings: dict[str, int | float],
    *,
    name: str,
    position: str,
    age: int | None,
    height_in: int | None,
    weight_lbs: int | None,
    overall: int | None,
    potential: int | None,
) -> dict[str, int]:
    adjusted = {key: clamp_int(value) for key, value in ratings.items()}
    speed = adjusted.get("speed")
    acceleration = adjusted.get("acceleration")
    agility = adjusted.get("agility")
    if speed is None and acceleration is None and agility is None:
        return adjusted

    caps: list[tuple[int, int, int]] = []
    if position.upper() == "WR":
        size_caps = receiver_size_caps(name, height_in, weight_lbs, overall, potential)
        if size_caps:
            caps.append(size_caps)
    veteran_caps = veteran_skill_caps(position, age)
    if veteran_caps:
        caps.append(veteran_caps)

    if not caps:
        return adjusted

    speed_cap = min(cap[0] for cap in caps)
    acceleration_cap = min(cap[1] for cap in caps)
    agility_cap = min(cap[2] for cap in caps)
    if speed is not None:
        adjusted["speed"] = min(speed, speed_cap)
    if acceleration is not None:
        adjusted["acceleration"] = min(acceleration, acceleration_cap)
    if agility is not None:
        adjusted["agility"] = min(agility, agility_cap)
    return adjusted
