"""Attribute-driven football match engine for NFL GM Sim.

This is intentionally a first playable engine, not the final physics model.
It uses the normalized 0-100 player_ratings table, depth charts, and a
tenths-of-a-second game clock. The design keeps the surface stable so future
work can swap in richer playbooks, coaching tendencies, injuries, weather,
and local LLM GM/coach logic without changing schedule/result storage.
"""

from __future__ import annotations

import math
import random
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterable

from engine.qb_behavior import (
    player_qb_behavior_table_exists,
    profile_from_mapping as qb_profile_from_mapping,
    qb_behavior_profile,
)
from engine.rb_behavior import (
    player_rb_behavior_table_exists,
    profile_from_mapping as rb_profile_from_mapping,
    rb_behavior_profile,
)
from engine.receiver_behavior import (
    player_receiver_behavior_table_exists,
    profile_from_mapping as receiver_profile_from_mapping,
    receiver_behavior_profile,
)
from engine.ol_behavior import (
    player_ol_behavior_table_exists,
    profile_from_mapping as ol_profile_from_mapping,
    ol_behavior_profile,
)
from engine.edge_behavior import (
    player_edge_behavior_table_exists,
    profile_from_mapping as edge_profile_from_mapping,
    edge_behavior_profile,
)
from engine.idl_behavior import (
    player_idl_behavior_table_exists,
    profile_from_mapping as idl_profile_from_mapping,
    idl_behavior_profile,
)
from engine.lb_behavior import (
    LB_POSITIONS,
    player_lb_behavior_table_exists,
    profile_from_mapping as lb_profile_from_mapping,
    lb_behavior_profile,
)
from engine.secondary_behavior import (
    SECONDARY_POSITIONS,
    player_secondary_behavior_table_exists,
    profile_from_mapping as secondary_profile_from_mapping,
    secondary_behavior_profile,
)
from engine.specialist_behavior import (
    player_specialist_behavior_table_exists,
    profile_from_mapping as specialist_profile_from_mapping,
    specialist_behavior_profile,
)
from engine import injury_model


ENGINE_VERSION = "0.1.7"
TENTHS_PER_SECOND = 10
REGULATION_QUARTER_TENTHS = 15 * 60 * TENTHS_PER_SECOND
OVERTIME_TENTHS = 10 * 60 * TENTHS_PER_SECOND
DEFAULT_SEASON = 2026

DEFENSIVE_SLOT_STARTER_RATE = {
    "LEDGE": 0.78,
    "REDGE": 0.78,
    "LDL": 0.70,
    "RDL": 0.70,
    "NT": 0.58,
    "MLB": 0.92,
    "WLB": 0.88,
    "SLB": 0.58,
    "LCB": 0.95,
    "RCB": 0.95,
    "NB": 0.76,
    "FS": 0.94,
    "SS": 0.94,
}

OFFENSIVE_SLOT_STARTER_RATE = {
    "QB": 0.998,
    "RB": 0.72,
    "FB": 0.46,
    "LT": 0.992,
    "LG": 0.992,
    "C": 0.995,
    "RG": 0.992,
    "RT": 0.992,
    "TE": 0.74,
    "LWR": 0.87,
    "RWR": 0.85,
    "SWR": 0.74,
}

DEVELOPMENTAL_ROTATION_SLOTS = {
    "RB",
    "FB",
    "TE",
    "LWR",
    "RWR",
    "SWR",
    "LEDGE",
    "REDGE",
    "LDL",
    "RDL",
    "NT",
    "SLB",
    "WLB",
    "MLB",
    "LCB",
    "RCB",
    "NB",
    "FS",
    "SS",
}

ROTATION_FATIGUE_THRESHOLDS = {
    "QB": 95,
    "LT": 92,
    "LG": 92,
    "C": 94,
    "RG": 92,
    "RT": 92,
    "RB": 30,
    "FB": 18,
    "TE": 38,
    "LWR": 48,
    "RWR": 46,
    "SWR": 46,
    "LEDGE": 30,
    "REDGE": 30,
    "LDL": 28,
    "RDL": 28,
    "NT": 22,
    "MLB": 56,
    "WLB": 52,
    "SLB": 34,
    "LCB": 62,
    "RCB": 62,
    "NB": 42,
    "FS": 64,
    "SS": 62,
}

SPECIAL_TEAMS_POSITION_PRIOR = {
    "LB": 1.18,
    "ILB": 1.18,
    "OLB": 1.14,
    "CB": 1.12,
    "NB": 1.12,
    "FS": 1.10,
    "SS": 1.10,
    "S": 1.10,
    "TE": 1.04,
    "RB": 1.02,
    "FB": 1.08,
    "WR": 1.00,
    "EDGE": 0.92,
    "IDL": 0.78,
    "DT": 0.78,
    "NT": 0.72,
}

SPECIAL_TEAMS_BLOCKER_PRIOR = {
    "TE": 1.12,
    "FB": 1.12,
    "LB": 1.04,
    "ILB": 1.04,
    "OLB": 1.02,
    "EDGE": 1.00,
    "RB": 0.98,
    "WR": 0.92,
    "CB": 0.88,
    "FS": 0.90,
    "SS": 0.90,
}


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def weighted_average(player: "PlayerSnapshot", weights: dict[str, float], default: float = 50.0) -> float:
    total_weight = sum(weights.values())
    if total_weight <= 0:
        return default
    return sum(player.rating(key, default) * weight for key, weight in weights.items()) / total_weight


def average(values: Iterable[float], default: float = 50.0) -> float:
    values = list(values)
    if not values:
        return default
    return sum(values) / len(values)


def sack_credit_weight(player: "PlayerSnapshot") -> float:
    score = weighted_average(player, SACK_CREDIT_WEIGHTS)
    explosive = average(
        [
            player.rating("speed_rush"),
            player.rating("finesse_rush"),
            player.rating("sack_finish"),
            player.rating("acceleration"),
        ]
    )
    anchor = average(
        [
            player.rating("gap_integrity"),
            player.rating("double_team_takeon"),
            player.rating("block_shedding"),
            player.rating("strength"),
        ]
    )
    weight = max(1.0, score - 43.0) ** 1.08
    if score >= 90:
        weight *= 1.05
    elif score >= 87:
        weight *= 1.00
    elif score >= 84:
        weight *= 1.00
    elif score < 78:
        weight *= 0.52
    elif score < 80:
        weight *= 0.62
    elif score < 82:
        weight *= 0.74
    elif score < 84:
        weight *= 0.84
    finish = player.rating("sack_finish")
    if finish < 78:
        weight *= 0.70
    elif finish < 80:
        weight *= 0.80
    if player.position in {"EDGE", "OLB", "DE"}:
        profile = edge_behavior_profile(player)
        behavior_multiplier = 1.0
        behavior_multiplier += (profile.finish_skill - 50) * 0.0030
        behavior_multiplier += (profile.counter_plan - 50) * 0.0018
        behavior_multiplier += (profile.getoff_timing - 50) * 0.0012
        if profile.speed_arc >= profile.power_collapse + 10:
            behavior_multiplier += (profile.speed_arc - 70) * 0.0012
        weight *= 0.92 * clamp(behavior_multiplier, 0.86, 1.06)
    if player.position in {"IDL", "DT", "NT"}:
        profile = idl_behavior_profile(player)
        behavior_multiplier = 1.0
        behavior_multiplier += (profile.finish_skill - 50) * 0.0030
        behavior_multiplier += (profile.penetration_burst - 50) * 0.0024
        behavior_multiplier += (profile.rush_counter_plan - 50) * 0.0018
        behavior_multiplier += (profile.power_collapse - 50) * 0.0012
        anchor_bias = average([profile.double_team_anchor, profile.gap_control]) - average(
            [profile.penetration_burst, profile.finish_skill, profile.rush_counter_plan]
        )
        if anchor_bias >= 8:
            behavior_multiplier *= clamp(1.0 - anchor_bias * 0.016, 0.62, 0.94)
        weight *= 0.86 * clamp(behavior_multiplier, 0.60, 1.04)
        if anchor >= explosive + 10:
            weight *= 0.68
    return max(0.05, weight)


def special_teams_coverage_weight(player: "PlayerSnapshot") -> float:
    profile = specialist_behavior_profile(player)
    return average(
        [
            profile.lane_release,
            profile.gunner_speed,
            profile.coverage_tackle,
            profile.penalty_control,
            player.rating("stamina"),
        ]
    )


def special_teams_return_weight(player: "PlayerSnapshot") -> float:
    profile = specialist_behavior_profile(player)
    return average(
        [
            weighted_average(player, YAC_WEIGHTS),
            player.rating("ball_security"),
            player.rating("play_recognition"),
            profile.return_lane_vision,
            profile.lane_release,
        ]
    )


def special_teams_block_weight(player: "PlayerSnapshot") -> float:
    profile = specialist_behavior_profile(player)
    return average(
        [
            profile.block_timing,
            profile.lane_release,
            player.rating("acceleration"),
            player.rating("play_recognition"),
            player.rating("discipline"),
        ]
    )


def clock_string(tenths: int) -> str:
    tenths = max(0, int(tenths))
    minutes = tenths // 600
    seconds = (tenths % 600) // 10
    tenth = tenths % 10
    return f"{minutes:02d}:{seconds:02d}.{tenth}"


def format_yardline(field_pos: int) -> str:
    field_pos = int(clamp(field_pos, 0, 100))
    if field_pos == 50:
        return "50"
    if field_pos < 50:
        return f"own {field_pos}"
    return f"opp {100 - field_pos}"


def weighted_choice(rng: random.Random, items: list[tuple[object, float]]):
    clean = [(item, max(0.01, weight)) for item, weight in items]
    total = sum(weight for _item, weight in clean)
    roll = rng.random() * total
    cursor = 0.0
    for item, weight in clean:
        cursor += weight
        if roll <= cursor:
            return item
    return clean[-1][0]


QB_PASS_WEIGHTS = {
    "pass_accuracy_short": 12,
    "pass_accuracy_mid": 16,
    "pass_accuracy_deep": 10,
    "throw_power": 8,
    "throw_release": 10,
    "platform_control": 9,
    "processing_speed": 14,
    "play_recognition": 12,
    "composure": 8,
    "discipline": 5,
}
QB_SCRAMBLE_WEIGHTS = {
    "speed": 12,
    "acceleration": 12,
    "agility": 12,
    "elusiveness": 12,
    "carry_vision": 8,
    "ball_security": 8,
    "platform_control": 6,
}
RB_RUN_WEIGHTS = {
    "carry_vision": 14,
    "run_patience": 12,
    "elusiveness": 10,
    "contact_power": 10,
    "balance": 10,
    "speed": 8,
    "acceleration": 10,
    "agility": 8,
    "ball_security": 8,
}
RECEIVER_WEIGHTS = {
    "route_timing": 14,
    "route_snap": 12,
    "release_vs_press": 8,
    "hands": 12,
    "catch_in_traffic": 8,
    "contested_catch": 6,
    "speed": 8,
    "acceleration": 8,
    "agility": 6,
    "composure": 4,
}
YAC_WEIGHTS = {
    "speed": 10,
    "acceleration": 10,
    "agility": 10,
    "elusiveness": 10,
    "balance": 8,
    "contact_power": 6,
    "ball_security": 4,
}
RUN_BLOCK_WEIGHTS = {
    "run_block_drive": 14,
    "reach_block": 10,
    "block_sustain": 12,
    "lead_block": 4,
    "strength": 10,
    "discipline": 4,
    "stamina": 3,
}
PASS_BLOCK_WEIGHTS = {
    "pass_block_speed": 12,
    "pass_block_power": 12,
    "pass_block_finesse": 12,
    "strength": 8,
    "processing_speed": 5,
    "discipline": 5,
    "stamina": 3,
}
PASS_RUSH_WEIGHTS = {
    "speed_rush": 10,
    "power_rush": 10,
    "finesse_rush": 8,
    "rush_plan": 8,
    "sack_finish": 8,
    "acceleration": 6,
    "strength": 6,
    "stamina": 3,
}
SACK_CREDIT_WEIGHTS = {
    "sack_finish": 14,
    "rush_plan": 10,
    "speed_rush": 9,
    "finesse_rush": 8,
    "power_rush": 7,
    "acceleration": 6,
    "strength": 2,
    "stamina": 2,
}
RUN_DEF_WEIGHTS = {
    "gap_integrity": 12,
    "run_diagnostics": 12,
    "block_shedding": 10,
    "double_team_takeon": 8,
    "edge_contain": 6,
    "strength": 8,
    "tackle_wrap": 8,
    "solo_tackle": 6,
}
COVERAGE_WEIGHTS = {
    "man_coverage": 11,
    "zone_coverage": 11,
    "press_coverage": 6,
    "coverage_communication": 6,
    "play_recognition": 8,
    "processing_speed": 6,
    "speed": 8,
    "agility": 8,
    "ball_skills": 6,
}
TACKLE_WEIGHTS = {
    "solo_tackle": 10,
    "assist_tackle": 4,
    "tackle_wrap": 10,
    "open_field_tackle": 10,
    "pursuit_angle": 6,
    "hit_power": 5,
    "strength": 4,
}
ASSIST_TACKLE_WEIGHTS = {
    "assist_tackle": 12,
    "tackle_wrap": 9,
    "pursuit_angle": 8,
    "play_recognition": 5,
    "speed": 4,
    "strength": 3,
}
KICK_WEIGHTS = {
    "kick_power": 9,
    "kick_accuracy": 12,
    "composure": 4,
}
PUNT_WEIGHTS = {
    "kick_power": 12,
    "kick_accuracy": 8,
    "composure": 3,
}
KICKER_POSITIONS = {"K", "PK"}
PUNTER_POSITIONS = {"P"}


SLOT_POSITION_FALLBACKS = {
    "QB": ["QB"],
    "RB": ["RB"],
    "FB": ["FB", "TE", "RB"],
    "LWR": ["WR"],
    "RWR": ["WR"],
    "SWR": ["WR"],
    "TE": ["TE"],
    "LT": ["OT", "OL"],
    "LG": ["OG", "C", "OL"],
    "C": ["C", "OG", "OL"],
    "RG": ["OG", "C", "OL"],
    "RT": ["OT", "OL"],
    "LEDGE": ["EDGE", "OLB", "DE"],
    "REDGE": ["EDGE", "OLB", "DE"],
    "LDL": ["IDL", "DT", "DE"],
    "NT": ["IDL", "DT", "NT"],
    "RDL": ["IDL", "DT", "DE"],
    "MLB": ["ILB", "LB"],
    "WLB": ["ILB", "LB", "OLB"],
    "SLB": ["ILB", "LB", "OLB", "EDGE"],
    "LCB": ["CB"],
    "RCB": ["CB"],
    "NB": ["NB", "CB", "SS", "FS", "S"],
    "FS": ["FS", "SS", "S", "CB"],
    "SS": ["SS", "FS", "S", "CB"],
    "PK": ["K"],
    "K": ["K"],
    "KO": ["K"],
    "PT": ["P"],
    "P": ["P"],
}


@dataclass
class PlayerSnapshot:
    player_id: int
    name: str
    position: str
    ratings: dict[str, int]
    role_scores: dict[str, float] = field(default_factory=dict)
    metadata: dict[str, object] = field(default_factory=dict)

    def rating(self, key: str, default: float = 50.0) -> float:
        return float(self.ratings.get(key, default))

    def role(self, key: str, default: float = 50.0) -> float:
        return float(self.role_scores.get(key, default))

    def general_score(self) -> float:
        role_anchor = max(self.role_scores.values(), default=0.0)
        universal = average(
            self.rating(key)
            for key in (
                "play_recognition",
                "processing_speed",
                "discipline",
                "composure",
                "consistency",
            )
        )
        athletic = average(
            self.rating(key)
            for key in ("speed", "acceleration", "agility", "strength", "stamina")
        )
        if role_anchor:
            return role_anchor * 0.70 + universal * 0.20 + athletic * 0.10
        return universal * 0.60 + athletic * 0.40


@dataclass
class TeamSnapshot:
    team_id: int
    abbreviation: str
    city: str
    nickname: str
    conference: str
    division: str
    roster: list[PlayerSnapshot]
    depth: dict[str, list[PlayerSnapshot]]
    wins: int = 0
    losses: int = 0
    ties: int = 0
    point_diff: int = 0

    @property
    def display_name(self) -> str:
        return f"{self.city} {self.nickname}"

    def candidates(self, slot: str) -> list[PlayerSnapshot]:
        slot = slot.upper()
        if self.depth.get(slot):
            return self.depth[slot]
        fallback_positions = SLOT_POSITION_FALLBACKS.get(slot, [slot])
        players = [p for p in self.roster if p.position in fallback_positions]
        return sorted(players, key=lambda p: self.score_for_slot(p, slot), reverse=True)

    def starter(self, slot: str) -> PlayerSnapshot:
        candidates = self.candidates(slot)
        if candidates:
            return candidates[0]
        if self.roster:
            return max(self.roster, key=lambda p: p.general_score())
        raise ValueError(f"{self.abbreviation} has no roster players available for {slot}.")

    def games_played(self) -> int:
        return int(self.wins + self.losses + self.ties)

    def win_pct(self) -> float:
        games = self.games_played()
        if games <= 0:
            return 0.5
        return (self.wins + self.ties * 0.5) / games

    def point_diff_per_game(self) -> float:
        return self.point_diff / max(1, self.games_played())

    def unique_starters(self, slots: list[str]) -> list[PlayerSnapshot]:
        selected = []
        used = set()
        for slot in slots:
            for player in self.candidates(slot):
                if player.player_id not in used:
                    selected.append(player)
                    used.add(player.player_id)
                    break
        return selected

    def depth_rank_for_player(self, player: PlayerSnapshot) -> int | None:
        best_rank: int | None = None
        for players in self.depth.values():
            for index, candidate in enumerate(players, start=1):
                if candidate.player_id == player.player_id:
                    if best_rank is None or index < best_rank:
                        best_rank = index
                    break
        return best_rank

    def score_for_slot(self, player: PlayerSnapshot, slot: str) -> float:
        slot = slot.upper()
        if slot == "QB":
            return max(player.role("pocket_qb"), player.role("scrambling_qb"), weighted_average(player, QB_PASS_WEIGHTS))
        if slot == "RB":
            return max(player.role("power_rb"), player.role("elusive_rb"), weighted_average(player, RB_RUN_WEIGHTS))
        if slot in {"LWR", "RWR", "SWR"}:
            return max(player.role("boundary_wr"), player.role("slot_wr"), weighted_average(player, RECEIVER_WEIGHTS))
        if slot == "TE":
            return max(player.role("inline_te"), player.role("move_te"), weighted_average(player, RECEIVER_WEIGHTS))
        if slot in {"LT", "RT"}:
            return max(player.role("pass_protecting_ot"), weighted_average(player, PASS_BLOCK_WEIGHTS))
        if slot in {"LG", "C", "RG"}:
            return max(player.role("interior_run_blocker"), weighted_average(player, RUN_BLOCK_WEIGHTS))
        if slot in {"LEDGE", "REDGE"}:
            return max(player.role("speed_edge"), player.role("power_edge"), weighted_average(player, PASS_RUSH_WEIGHTS))
        if slot in {"LDL", "NT", "RDL"}:
            return max(player.role("interior_rusher"), player.role("nose_run_stopping_dt"), weighted_average(player, RUN_DEF_WEIGHTS))
        if slot in {"MLB", "WLB", "SLB"}:
            score = max(player.role("box_lb"), player.role("coverage_lb"), weighted_average(player, TACKLE_WEIGHTS))
            if player.position in LB_POSITIONS:
                profile = lb_behavior_profile(player)
                if slot == "MLB":
                    score += (profile.trigger_quickness - 50) * 0.018
                    score += (profile.gap_fit_discipline - 50) * 0.018
                    score += (profile.zone_landmark_depth - 50) * 0.010
                    score += (profile.tackle_finish - 50) * 0.012
                elif slot == "WLB":
                    score += (profile.scrape_range - 50) * 0.020
                    score += (profile.zone_landmark_depth - 50) * 0.014
                    score += (profile.rally_support - 50) * 0.010
                else:
                    score += (profile.gap_fit_discipline - 50) * 0.014
                    score += (profile.traffic_navigation - 50) * 0.012
                    score += (profile.blitz_timing - 50) * 0.012
            return score
        if slot in {"LCB", "RCB", "NB"}:
            score = max(player.role("man_cb"), player.role("zone_cb"), weighted_average(player, COVERAGE_WEIGHTS))
            if player.position in SECONDARY_POSITIONS:
                profile = secondary_behavior_profile(player)
                if slot == "NB":
                    score += (profile.slot_traffic - 50) * 0.024
                    score += (profile.break_trigger - 50) * 0.014
                    score += (profile.tackle_finish - 50) * 0.010
                    score += (profile.zone_eye_discipline - 50) * 0.010
                else:
                    score += (profile.man_mirror - 50) * 0.020
                    score += (profile.press_timing - 50) * 0.014
                    score += (profile.break_trigger - 50) * 0.012
                    score += (profile.deep_range - 50) * 0.006
            return score
        if slot in {"FS", "SS"}:
            score = max(player.role("deep_safety"), player.role("box_safety"), weighted_average(player, COVERAGE_WEIGHTS))
            if player.position in SECONDARY_POSITIONS:
                profile = secondary_behavior_profile(player)
                if slot == "FS":
                    score += (profile.deep_range - 50) * 0.024
                    score += (profile.zone_eye_discipline - 50) * 0.018
                    score += (profile.ball_play_timing - 50) * 0.012
                    score += (profile.break_trigger - 50) * 0.010
                else:
                    score += (profile.run_support_fit - 50) * 0.018
                    score += (profile.tackle_finish - 50) * 0.016
                    score += (profile.slot_traffic - 50) * 0.012
                    score += (profile.zone_eye_discipline - 50) * 0.010
            return score
        if slot in {"PK", "K", "KO"}:
            score = weighted_average(player, KICK_WEIGHTS)
            profile = specialist_behavior_profile(player)
            if slot == "KO":
                score += (profile.kickoff_control - 50) * 0.020
            else:
                score += (profile.kick_operation - 50) * 0.020
            return score
        if slot in {"PT", "P"}:
            score = weighted_average(player, PUNT_WEIGHTS)
            profile = specialist_behavior_profile(player)
            score += (profile.punt_hang_time - 50) * 0.014
            score += (profile.punt_placement - 50) * 0.016
            return score
        if slot == "LS":
            profile = specialist_behavior_profile(player)
            return player.general_score() * 0.25 + profile.snap_accuracy * 0.50 + profile.penalty_control * 0.15 + profile.coverage_tackle * 0.10
        return player.general_score()

    def offensive_line(self) -> list[PlayerSnapshot]:
        return self.unique_starters(["LT", "LG", "C", "RG", "RT"])

    def receiving_options(self) -> list[PlayerSnapshot]:
        options = self.unique_starters(["LWR", "RWR", "SWR", "TE", "RB"])
        return options or self.roster[:5]

    def defensive_front(self) -> list[PlayerSnapshot]:
        return self.unique_starters(["LEDGE", "LDL", "NT", "RDL", "REDGE"])

    def linebackers(self) -> list[PlayerSnapshot]:
        return self.unique_starters(["MLB", "WLB", "SLB"])

    def secondary(self) -> list[PlayerSnapshot]:
        return self.unique_starters(["LCB", "RCB", "NB", "FS", "SS"])

    def run_block_score(self) -> float:
        blockers = self.offensive_line() + self.unique_starters(["TE", "FB"])
        scores = []
        for player in blockers:
            score = weighted_average(player, RUN_BLOCK_WEIGHTS)
            if player.position in {"OT", "OG", "C"}:
                profile = ol_behavior_profile(player)
                score += (profile.drive_finish - 50) * 0.035
                score += (profile.combo_timing - 50) * 0.026
                score += (profile.reach_range - 50) * 0.015
                score += (profile.second_level_climb - 50) * 0.010
            scores.append(score)
        return average(scores)

    def pass_block_score(self) -> float:
        blockers = self.offensive_line() + self.unique_starters(["TE"])
        scores = []
        for player in blockers:
            score = weighted_average(player, PASS_BLOCK_WEIGHTS)
            if player.position in {"OT", "OG", "C"}:
                profile = ol_behavior_profile(player)
                score += (profile.pass_set_patience - 50) * 0.018
                score += (profile.mirror_vs_speed - 50) * 0.024
                score += (profile.anchor_vs_power - 50) * 0.024
                score += (profile.hand_timing - 50) * 0.018
                score += (profile.stunt_awareness - 50) * 0.014
            scores.append(score)
        return average(scores)

    def offensive_line_profile_summary(self) -> dict[str, float]:
        profiles = [ol_behavior_profile(player) for player in self.offensive_line() if player.position in {"OT", "OG", "C"}]
        if not profiles:
            return {}
        return {
            "pass_set_patience": average(profile.pass_set_patience for profile in profiles),
            "mirror_vs_speed": average(profile.mirror_vs_speed for profile in profiles),
            "anchor_vs_power": average(profile.anchor_vs_power for profile in profiles),
            "stunt_awareness": average(profile.stunt_awareness for profile in profiles),
            "drive_finish": average(profile.drive_finish for profile in profiles),
            "reach_range": average(profile.reach_range for profile in profiles),
            "combo_timing": average(profile.combo_timing for profile in profiles),
            "second_level_climb": average(profile.second_level_climb for profile in profiles),
            "penalty_control": average(profile.penalty_control for profile in profiles),
        }

    def run_defense_score(self) -> float:
        defenders = self.defensive_front() + self.linebackers()
        linebacker_ids = {player.player_id for player in self.linebackers()}
        scores = []
        for player in defenders:
            score = weighted_average(player, RUN_DEF_WEIGHTS)
            if player.position in LB_POSITIONS and player.player_id in linebacker_ids:
                profile = lb_behavior_profile(player)
                score += (profile.trigger_quickness - 50) * 0.022
                score += (profile.gap_fit_discipline - 50) * 0.026
                score += (profile.scrape_range - 50) * 0.016
                score += (profile.traffic_navigation - 50) * 0.016
                score += (profile.tackle_finish - 50) * 0.012
            elif player.position in {"EDGE", "OLB", "DE"}:
                profile = edge_behavior_profile(player)
                score += (profile.contain_discipline - 50) * 0.024
                score += (profile.run_squeeze - 50) * 0.022
                score += (profile.backside_pursuit - 50) * 0.010
            elif player.position in {"IDL", "DT", "NT"}:
                profile = idl_behavior_profile(player)
                score += (profile.double_team_anchor - 50) * 0.024
                score += (profile.gap_control - 50) * 0.026
                score += (profile.block_shed_timing - 50) * 0.020
            scores.append(score)
        return average(scores)

    def pass_rush_score(self, rushers: list[PlayerSnapshot] | None = None) -> float:
        rushers = rushers or self.defensive_front()
        scores = []
        for player in rushers:
            score = weighted_average(player, PASS_RUSH_WEIGHTS)
            if player.position in {"EDGE", "OLB", "DE"}:
                profile = edge_behavior_profile(player)
                score += (profile.getoff_timing - 50) * 0.016
                score += (profile.speed_arc - 50) * 0.016
                score += (profile.power_collapse - 50) * 0.014
                score += (profile.counter_plan - 50) * 0.014
                score += (profile.stunt_timing - 50) * 0.010
                score += (profile.finish_skill - 50) * 0.008
            elif player.position in {"IDL", "DT", "NT"}:
                profile = idl_behavior_profile(player)
                score += (profile.getoff_timing - 50) * 0.010
                score += (profile.penetration_burst - 50) * 0.014
                score += (profile.power_collapse - 50) * 0.014
                score += (profile.stunt_timing - 50) * 0.010
                score += (profile.rush_counter_plan - 50) * 0.012
                score += (profile.finish_skill - 50) * 0.006
            scores.append(score)
        lb_profiles = [lb_behavior_profile(player) for player in self.linebackers() if player.position in LB_POSITIONS]
        blitz_bonus = 0.0
        if lb_profiles:
            blitz_bonus += max(0.0, average(profile.blitz_timing for profile in lb_profiles) - 50) * 0.007
            blitz_bonus += max(0.0, average(profile.trigger_quickness for profile in lb_profiles) - 55) * 0.003
        return average(scores) + blitz_bonus

    def coverage_score(self) -> float:
        defenders = self.secondary() + self.linebackers()
        scores = []
        for player in defenders:
            score = weighted_average(player, COVERAGE_WEIGHTS)
            if player.position in LB_POSITIONS:
                profile = lb_behavior_profile(player)
                score += (profile.zone_landmark_depth - 50) * 0.022
                score += (profile.man_match_carry - 50) * 0.016
                score += (profile.scrape_range - 50) * 0.008
                score += (profile.rally_support - 50) * 0.006
            elif player.position in SECONDARY_POSITIONS:
                profile = secondary_behavior_profile(player)
                score += (profile.man_mirror - 50) * 0.014
                score += (profile.zone_eye_discipline - 50) * 0.014
                score += (profile.break_trigger - 50) * 0.012
                score += (profile.deep_range - 50) * 0.008
                score += (profile.ball_play_timing - 50) * 0.008
                score += (profile.slot_traffic - 50) * 0.004
            scores.append(score)
        return average(scores)

    def tackling_score(self) -> float:
        defenders = self.defensive_front() + self.linebackers() + self.secondary()
        scores = []
        for player in defenders:
            score = weighted_average(player, TACKLE_WEIGHTS)
            if player.position in LB_POSITIONS:
                profile = lb_behavior_profile(player)
                score += (profile.tackle_finish - 50) * 0.024
                score += (profile.rally_support - 50) * 0.014
                score += (profile.scrape_range - 50) * 0.010
                score += (profile.traffic_navigation - 50) * 0.008
            elif player.position in SECONDARY_POSITIONS:
                profile = secondary_behavior_profile(player)
                score += (profile.tackle_finish - 50) * 0.020
                score += (profile.run_support_fit - 50) * 0.014
                score += (profile.slot_traffic - 50) * 0.008
            scores.append(score)
        return average(scores)

    def discipline_score(self) -> float:
        starters = (
            self.offensive_line()
            + self.receiving_options()
            + self.defensive_front()
            + self.linebackers()
            + self.secondary()
        )
        values = []
        for player in starters:
            if player.position in {"OT", "OG", "C"}:
                profile = ol_behavior_profile(player)
                values.append(player.rating("discipline") * 0.80 + profile.penalty_control * 0.20)
            elif player.position in {"EDGE", "OLB", "DE"}:
                profile = edge_behavior_profile(player)
                values.append(player.rating("discipline") * 0.82 + profile.rush_discipline * 0.18)
            elif player.position in {"IDL", "DT", "NT"}:
                profile = idl_behavior_profile(player)
                values.append(player.rating("discipline") * 0.84 + profile.rush_discipline * 0.16)
            elif player.position in LB_POSITIONS:
                profile = lb_behavior_profile(player)
                values.append(player.rating("discipline") * 0.80 + profile.penalty_control * 0.20)
            elif player.position in SECONDARY_POSITIONS:
                profile = secondary_behavior_profile(player)
                values.append(player.rating("discipline") * 0.80 + profile.penalty_control * 0.20)
            else:
                values.append(player.rating("discipline"))
        return average(values)


@dataclass
class PlayEvent:
    play_number: int
    drive_number: int
    quarter: int
    clock_tenths: int
    offense_team_id: int
    defense_team_id: int
    down: int
    distance: int
    yardline: int
    play_type: str
    concept: str
    yards_gained: int = 0
    offense_player_id: int | None = None
    target_player_id: int | None = None
    defense_player_id: int | None = None
    is_touchdown: int = 0
    is_turnover: int = 0
    clock_elapsed_tenths: int = 0
    runoff_tenths: int = 0
    description: str = ""


@dataclass
class DriveRecord:
    drive_number: int
    offense_team_id: int
    defense_team_id: int
    start_quarter: int
    start_clock_tenths: int
    start_yardline: int
    end_quarter: int = 0
    end_clock_tenths: int = 0
    end_yardline: int = 0
    result: str = ""
    plays: int = 0
    yards: int = 0
    points: int = 0
    time_elapsed_tenths: int = 0


@dataclass(frozen=True)
class PenaltyFlag:
    label: str
    side: str
    yards: int
    timing: str
    enforcement: str = "previous"
    no_play: bool = False
    automatic_first_down: bool = False
    loss_of_down: bool = False
    spot_yards: int | None = None
    personal_foul: bool = False


@dataclass(frozen=True)
class PenaltyDecision:
    field_pos: int
    down: int
    distance: int
    enforced_yards: int
    first_down: bool = False
    turnover_on_downs: bool = False
    keeps_play: bool = False


@dataclass
class GameResult:
    schedule_game_id: int | None
    season: int
    week: int | None
    away: TeamSnapshot
    home: TeamSnapshot
    away_score: int
    home_score: int
    seed: int
    plays: list[PlayEvent]
    drives: list[DriveRecord]
    team_stats: dict[int, Counter]
    player_stats: dict[int, Counter]
    injury_events: list[injury_model.InjuryEvent] = field(default_factory=list)
    status: str = "final"


def table_columns(con: sqlite3.Connection, table_name: str) -> set[str]:
    rows = con.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {row["name"] if isinstance(row, sqlite3.Row) else row[1] for row in rows}


def ensure_column(con: sqlite3.Connection, table_name: str, column_name: str, definition: str) -> None:
    if column_name not in table_columns(con, table_name):
        con.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}")


def ensure_schema(con: sqlite3.Connection) -> None:
    injury_model.ensure_schema(con)
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS game_sim_runs (
            run_id INTEGER PRIMARY KEY AUTOINCREMENT,
            schedule_game_id INTEGER REFERENCES season_games(game_id) ON DELETE SET NULL,
            season INTEGER NOT NULL,
            week INTEGER,
            away_team_id INTEGER NOT NULL REFERENCES teams(team_id) ON DELETE CASCADE,
            home_team_id INTEGER NOT NULL REFERENCES teams(team_id) ON DELETE CASCADE,
            seed INTEGER NOT NULL,
            engine_version TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'final',
            away_score INTEGER NOT NULL,
            home_score INTEGER NOT NULL,
            total_plays INTEGER NOT NULL,
            total_drives INTEGER NOT NULL,
            notes TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS game_sim_drives (
            run_id INTEGER NOT NULL REFERENCES game_sim_runs(run_id) ON DELETE CASCADE,
            drive_number INTEGER NOT NULL,
            offense_team_id INTEGER NOT NULL REFERENCES teams(team_id) ON DELETE CASCADE,
            defense_team_id INTEGER NOT NULL REFERENCES teams(team_id) ON DELETE CASCADE,
            start_quarter INTEGER NOT NULL,
            start_clock_tenths INTEGER NOT NULL,
            end_quarter INTEGER NOT NULL,
            end_clock_tenths INTEGER NOT NULL,
            start_yardline INTEGER NOT NULL,
            end_yardline INTEGER NOT NULL,
            result TEXT NOT NULL,
            plays INTEGER NOT NULL,
            yards INTEGER NOT NULL,
            points INTEGER NOT NULL,
            time_elapsed_tenths INTEGER NOT NULL,
            PRIMARY KEY(run_id, drive_number)
        );

        CREATE TABLE IF NOT EXISTS game_sim_plays (
            run_id INTEGER NOT NULL REFERENCES game_sim_runs(run_id) ON DELETE CASCADE,
            play_number INTEGER NOT NULL,
            drive_number INTEGER NOT NULL,
            quarter INTEGER NOT NULL,
            clock_tenths INTEGER NOT NULL,
            offense_team_id INTEGER NOT NULL REFERENCES teams(team_id) ON DELETE CASCADE,
            defense_team_id INTEGER NOT NULL REFERENCES teams(team_id) ON DELETE CASCADE,
            down INTEGER NOT NULL,
            distance INTEGER NOT NULL,
            yardline INTEGER NOT NULL,
            play_type TEXT NOT NULL,
            concept TEXT,
            offense_player_id INTEGER REFERENCES players(player_id) ON DELETE SET NULL,
            target_player_id INTEGER REFERENCES players(player_id) ON DELETE SET NULL,
            defense_player_id INTEGER REFERENCES players(player_id) ON DELETE SET NULL,
            yards_gained INTEGER NOT NULL DEFAULT 0,
            is_touchdown INTEGER NOT NULL DEFAULT 0,
            is_turnover INTEGER NOT NULL DEFAULT 0,
            clock_elapsed_tenths INTEGER NOT NULL DEFAULT 0,
            runoff_tenths INTEGER NOT NULL DEFAULT 0,
            description TEXT,
            PRIMARY KEY(run_id, play_number)
        );

        CREATE INDEX IF NOT EXISTS idx_game_sim_plays_run_drive
            ON game_sim_plays(run_id, drive_number, play_number);

        CREATE TABLE IF NOT EXISTS game_team_stats (
            run_id INTEGER NOT NULL REFERENCES game_sim_runs(run_id) ON DELETE CASCADE,
            team_id INTEGER NOT NULL REFERENCES teams(team_id) ON DELETE CASCADE,
            stat_key TEXT NOT NULL,
            stat_value REAL NOT NULL,
            PRIMARY KEY(run_id, team_id, stat_key)
        );

        CREATE TABLE IF NOT EXISTS game_player_stats (
            run_id INTEGER NOT NULL REFERENCES game_sim_runs(run_id) ON DELETE CASCADE,
            player_id INTEGER NOT NULL REFERENCES players(player_id) ON DELETE CASCADE,
            team_id INTEGER NOT NULL REFERENCES teams(team_id) ON DELETE CASCADE,
            stat_key TEXT NOT NULL,
            stat_value REAL NOT NULL,
            PRIMARY KEY(run_id, player_id, stat_key)
        );

        CREATE TABLE IF NOT EXISTS season_team_records (
            season INTEGER NOT NULL,
            team_id INTEGER NOT NULL REFERENCES teams(team_id) ON DELETE CASCADE,
            wins INTEGER NOT NULL DEFAULT 0,
            losses INTEGER NOT NULL DEFAULT 0,
            ties INTEGER NOT NULL DEFAULT 0,
            points_for INTEGER NOT NULL DEFAULT 0,
            points_against INTEGER NOT NULL DEFAULT 0,
            conference_wins INTEGER NOT NULL DEFAULT 0,
            conference_losses INTEGER NOT NULL DEFAULT 0,
            conference_ties INTEGER NOT NULL DEFAULT 0,
            division_wins INTEGER NOT NULL DEFAULT 0,
            division_losses INTEGER NOT NULL DEFAULT 0,
            division_ties INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY(season, team_id)
        );

        CREATE TABLE IF NOT EXISTS season_team_stats (
            season INTEGER NOT NULL,
            team_id INTEGER NOT NULL REFERENCES teams(team_id) ON DELETE CASCADE,
            stat_key TEXT NOT NULL,
            stat_value REAL NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY(season, team_id, stat_key)
        );

        CREATE TABLE IF NOT EXISTS season_player_stats (
            season INTEGER NOT NULL,
            player_id INTEGER NOT NULL REFERENCES players(player_id) ON DELETE CASCADE,
            team_id INTEGER NOT NULL REFERENCES teams(team_id) ON DELETE CASCADE,
            stat_key TEXT NOT NULL,
            stat_value REAL NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY(season, player_id, team_id, stat_key)
        );

        CREATE INDEX IF NOT EXISTS idx_season_player_stats_leaders
            ON season_player_stats(season, stat_key, stat_value DESC);

        DROP VIEW IF EXISTS season_standings_view;
        CREATE VIEW season_standings_view AS
        SELECT
            str.season,
            t.team_id,
            t.abbreviation,
            t.city,
            t.nickname,
            t.conference,
            t.division,
            COALESCE(str.wins, 0) AS wins,
            COALESCE(str.losses, 0) AS losses,
            COALESCE(str.ties, 0) AS ties,
            COALESCE(str.points_for, 0) AS points_for,
            COALESCE(str.points_against, 0) AS points_against,
            COALESCE(str.points_for, 0) - COALESCE(str.points_against, 0) AS point_diff,
            COALESCE(str.conference_wins, 0) AS conference_wins,
            COALESCE(str.conference_losses, 0) AS conference_losses,
            COALESCE(str.conference_ties, 0) AS conference_ties,
            COALESCE(str.division_wins, 0) AS division_wins,
            COALESCE(str.division_losses, 0) AS division_losses,
            COALESCE(str.division_ties, 0) AS division_ties,
            CASE
                WHEN COALESCE(str.wins, 0) + COALESCE(str.losses, 0) + COALESCE(str.ties, 0) = 0
                THEN 0.0
                ELSE (COALESCE(str.wins, 0) + COALESCE(str.ties, 0) * 0.5)
                     / (COALESCE(str.wins, 0) + COALESCE(str.losses, 0) + COALESCE(str.ties, 0))
            END AS win_pct,
            str.updated_at
        FROM teams t
        LEFT JOIN season_team_records str ON str.team_id = t.team_id;

        DROP VIEW IF EXISTS season_player_stats_view;
        CREATE VIEW season_player_stats_view AS
        SELECT
            sps.season,
            sps.player_id,
            p.first_name || ' ' || p.last_name AS player_name,
            p.position,
            sps.team_id,
            t.abbreviation AS team,
            sps.stat_key,
            sps.stat_value,
            sps.updated_at
        FROM season_player_stats sps
        JOIN players p ON p.player_id = sps.player_id
        JOIN teams t ON t.team_id = sps.team_id;

        DROP VIEW IF EXISTS season_team_stats_view;
        CREATE VIEW season_team_stats_view AS
        SELECT
            sts.season,
            sts.team_id,
            t.abbreviation AS team,
            t.city,
            t.nickname,
            sts.stat_key,
            sts.stat_value,
            sts.updated_at
        FROM season_team_stats sts
        JOIN teams t ON t.team_id = sts.team_id;
        """
    )
    ensure_column(con, "game_sim_runs", "counts_for_stats", "INTEGER NOT NULL DEFAULT 1")
    ensure_column(con, "game_sim_runs", "counts_for_standings", "INTEGER NOT NULL DEFAULT 1")
    ensure_column(con, "game_sim_runs", "superseded_by_run_id", "INTEGER")
    con.executescript(
        """
        CREATE INDEX IF NOT EXISTS idx_game_sim_runs_schedule
            ON game_sim_runs(schedule_game_id, counts_for_stats, counts_for_standings);
        """
    )


def load_team(con: sqlite3.Connection, team_id: int, season: int, as_of_date: str | None = None) -> TeamSnapshot:
    injury_model.ensure_schema(con)
    team_row = con.execute("SELECT * FROM teams WHERE team_id = ?", (team_id,)).fetchone()
    if not team_row:
        raise ValueError(f"Team id not found: {team_id}")

    player_rows = con.execute(
        """
        SELECT p.*
        FROM players p
        LEFT JOIN roster_status_types rst ON rst.status_code = p.status
        WHERE p.team_id = ?
          AND COALESCE(
                rst.counts_against_roster_limit,
                CASE WHEN COALESCE(p.status, 'Active') NOT IN ('Retired', 'Free Agent') THEN 1 ELSE 0 END
              ) = 1
        """,
        (team_id,),
    ).fetchall()
    player_ids = [int(row["player_id"]) for row in player_rows]
    unavailable_ids = injury_model.unavailable_player_ids(con, player_ids, as_of_date)
    if unavailable_ids:
        player_rows = [row for row in player_rows if int(row["player_id"]) not in unavailable_ids]
        player_ids = [int(row["player_id"]) for row in player_rows]
    if not player_ids:
        raise ValueError(f"{team_row['abbreviation']} has no roster players.")

    placeholders = ",".join("?" for _ in player_ids)
    rating_rows = con.execute(
        f"""
        SELECT player_id, rating_key, rating_value
        FROM player_ratings
        WHERE season = ? AND player_id IN ({placeholders})
        """,
        (season, *player_ids),
    ).fetchall()
    ratings_by_player: dict[int, dict[str, int]] = defaultdict(dict)
    for row in rating_rows:
        ratings_by_player[int(row["player_id"])][row["rating_key"]] = int(row["rating_value"])

    role_rows = con.execute(
        f"""
        SELECT player_id, role_key, role_score
        FROM player_role_scores
        WHERE season = ? AND player_id IN ({placeholders})
        """,
        (season, *player_ids),
    ).fetchall()
    role_by_player: dict[int, dict[str, float]] = defaultdict(dict)
    for row in role_rows:
        role_by_player[int(row["player_id"])][row["role_key"]] = float(row["role_score"])

    qb_behavior_by_player: dict[int, tuple[object, str]] = {}
    if player_qb_behavior_table_exists(con):
        profile_rows = con.execute(
            f"""
            SELECT *
            FROM player_qb_behavior_profiles
            WHERE season = ? AND player_id IN ({placeholders})
            """,
            (season, *player_ids),
        ).fetchall()
        for row in profile_rows:
            qb_behavior_by_player[int(row["player_id"])] = (qb_profile_from_mapping(dict(row)), str(row["source"]))

    rb_behavior_by_player: dict[int, tuple[object, str]] = {}
    if player_rb_behavior_table_exists(con):
        profile_rows = con.execute(
            f"""
            SELECT *
            FROM player_rb_behavior_profiles
            WHERE season = ? AND player_id IN ({placeholders})
            """,
            (season, *player_ids),
        ).fetchall()
        for row in profile_rows:
            rb_behavior_by_player[int(row["player_id"])] = (rb_profile_from_mapping(dict(row)), str(row["source"]))

    receiver_behavior_by_player: dict[int, tuple[object, str]] = {}
    if player_receiver_behavior_table_exists(con):
        profile_rows = con.execute(
            f"""
            SELECT *
            FROM player_receiver_behavior_profiles
            WHERE season = ? AND player_id IN ({placeholders})
            """,
            (season, *player_ids),
        ).fetchall()
        for row in profile_rows:
            receiver_behavior_by_player[int(row["player_id"])] = (
                receiver_profile_from_mapping(dict(row)),
                str(row["source"]),
            )

    ol_behavior_by_player: dict[int, tuple[object, str]] = {}
    if player_ol_behavior_table_exists(con):
        profile_rows = con.execute(
            f"""
            SELECT *
            FROM player_ol_behavior_profiles
            WHERE season = ? AND player_id IN ({placeholders})
            """,
            (season, *player_ids),
        ).fetchall()
        for row in profile_rows:
            ol_behavior_by_player[int(row["player_id"])] = (ol_profile_from_mapping(dict(row)), str(row["source"]))

    edge_behavior_by_player: dict[int, tuple[object, str]] = {}
    if player_edge_behavior_table_exists(con):
        profile_rows = con.execute(
            f"""
            SELECT *
            FROM player_edge_behavior_profiles
            WHERE season = ? AND player_id IN ({placeholders})
            """,
            (season, *player_ids),
        ).fetchall()
        for row in profile_rows:
            edge_behavior_by_player[int(row["player_id"])] = (edge_profile_from_mapping(dict(row)), str(row["source"]))

    idl_behavior_by_player: dict[int, tuple[object, str]] = {}
    if player_idl_behavior_table_exists(con):
        profile_rows = con.execute(
            f"""
            SELECT *
            FROM player_idl_behavior_profiles
            WHERE season = ? AND player_id IN ({placeholders})
            """,
            (season, *player_ids),
        ).fetchall()
        for row in profile_rows:
            idl_behavior_by_player[int(row["player_id"])] = (idl_profile_from_mapping(dict(row)), str(row["source"]))

    lb_behavior_by_player: dict[int, tuple[object, str]] = {}
    if player_lb_behavior_table_exists(con):
        profile_rows = con.execute(
            f"""
            SELECT *
            FROM player_lb_behavior_profiles
            WHERE season = ? AND player_id IN ({placeholders})
            """,
            (season, *player_ids),
        ).fetchall()
        for row in profile_rows:
            lb_behavior_by_player[int(row["player_id"])] = (lb_profile_from_mapping(dict(row)), str(row["source"]))

    secondary_behavior_by_player: dict[int, tuple[object, str]] = {}
    if player_secondary_behavior_table_exists(con):
        profile_rows = con.execute(
            f"""
            SELECT *
            FROM player_secondary_behavior_profiles
            WHERE season = ? AND player_id IN ({placeholders})
            """,
            (season, *player_ids),
        ).fetchall()
        for row in profile_rows:
            secondary_behavior_by_player[int(row["player_id"])] = (
                secondary_profile_from_mapping(dict(row)),
                str(row["source"]),
            )

    specialist_behavior_by_player: dict[int, tuple[object, str]] = {}
    if player_specialist_behavior_table_exists(con):
        profile_rows = con.execute(
            f"""
            SELECT *
            FROM player_specialist_behavior_profiles
            WHERE season = ? AND player_id IN ({placeholders})
            """,
            (season, *player_ids),
        ).fetchall()
        for row in profile_rows:
            specialist_behavior_by_player[int(row["player_id"])] = (
                specialist_profile_from_mapping(dict(row)),
                str(row["source"]),
            )

    injury_context = injury_model.injury_context_by_player(con, player_ids, as_of_date)
    active_injuries = injury_model.active_injuries_by_player(con, player_ids)
    draft_context: dict[int, dict[str, object]] = {}
    has_draft_context = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table', 'view') AND name = 'draft_prospects'"
    ).fetchone() and con.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table', 'view') AND name = 'draft_picks'"
    ).fetchone()
    if has_draft_context:
        for row in con.execute(
            f"""
            SELECT
                dp.player_id,
                dpk.draft_year,
                dpk.round AS draft_round,
                dpk.pick_number,
                dpk.pick_in_round,
                dp.true_rank,
                dp.projected_round
            FROM draft_prospects dp
            LEFT JOIN draft_picks dpk ON dpk.pick_id = dp.selected_pick_id
            WHERE dp.player_id IN ({placeholders})
            """,
            player_ids,
        ).fetchall():
            draft_context[int(row["player_id"])] = {
                "draft_year": int(row["draft_year"]) if row["draft_year"] is not None else None,
                "draft_round": int(row["draft_round"]) if row["draft_round"] is not None else None,
                "draft_pick_number": int(row["pick_number"]) if row["pick_number"] is not None else None,
                "draft_pick_in_round": int(row["pick_in_round"]) if row["pick_in_round"] is not None else None,
                "true_rank": int(row["true_rank"]) if row["true_rank"] is not None else None,
                "projected_round": int(row["projected_round"]) if row["projected_round"] is not None else None,
            }
    season_stats: dict[int, dict[str, float]] = defaultdict(dict)
    for row in con.execute(
        f"""
        SELECT player_id, stat_key, SUM(stat_value) AS stat_value
        FROM season_player_stats
        WHERE season = ?
          AND player_id IN ({placeholders})
        GROUP BY player_id, stat_key
        """,
        (season, *player_ids),
    ).fetchall():
        season_stats[int(row["player_id"])][str(row["stat_key"])] = float(row["stat_value"] or 0)
    record_row = con.execute(
        """
        SELECT wins, losses, ties, points_for, points_against
        FROM season_team_records
        WHERE season = ? AND team_id = ?
        """,
        (season, team_id),
    ).fetchone()

    players_by_id = {}
    roster = []
    for row in player_rows:
        name = f"{row['first_name']} {row['last_name']}"
        player_id = int(row["player_id"])
        player_context = injury_context.get(player_id, {})
        metadata = {
            "age": int(row["age"] or 26),
            "years_exp": int(row["years_exp"] or 0),
            "is_rookie": int(row["is_rookie"] or 0),
            "potential": int(row["potential"] or row["overall"] or 50),
            "overall": int(row["overall"] or 50),
            "dev_trait": row["dev_trait"] or "Normal",
            "weight_lbs": int(row["weight_lbs"] or 220),
            "injury_prone": int(row["injury_prone"] or 50),
            "status": row["status"] or "Active",
            "injury_history_risk": float(player_context.get("risk_score") or 0.0),
            "injury_body_risks": player_context.get("body_risks", {}),
            "active_injuries": active_injuries.get(player_id, []),
            "season_stats": season_stats.get(player_id, {}),
        }
        metadata.update(draft_context.get(player_id, {}))
        if player_id in qb_behavior_by_player:
            profile, source = qb_behavior_by_player[player_id]
            metadata["qb_behavior_profile"] = profile
            metadata["qb_behavior_source"] = source
        if player_id in rb_behavior_by_player:
            profile, source = rb_behavior_by_player[player_id]
            metadata["rb_behavior_profile"] = profile
            metadata["rb_behavior_source"] = source
        if player_id in receiver_behavior_by_player:
            profile, source = receiver_behavior_by_player[player_id]
            metadata["receiver_behavior_profile"] = profile
            metadata["receiver_behavior_source"] = source
        if player_id in ol_behavior_by_player:
            profile, source = ol_behavior_by_player[player_id]
            metadata["ol_behavior_profile"] = profile
            metadata["ol_behavior_source"] = source
        if player_id in edge_behavior_by_player:
            profile, source = edge_behavior_by_player[player_id]
            metadata["edge_behavior_profile"] = profile
            metadata["edge_behavior_source"] = source
        if player_id in idl_behavior_by_player:
            profile, source = idl_behavior_by_player[player_id]
            metadata["idl_behavior_profile"] = profile
            metadata["idl_behavior_source"] = source
        if player_id in lb_behavior_by_player:
            profile, source = lb_behavior_by_player[player_id]
            metadata["lb_behavior_profile"] = profile
            metadata["lb_behavior_source"] = source
        if player_id in secondary_behavior_by_player:
            profile, source = secondary_behavior_by_player[player_id]
            metadata["secondary_behavior_profile"] = profile
            metadata["secondary_behavior_source"] = source
        if player_id in specialist_behavior_by_player:
            profile, source = specialist_behavior_by_player[player_id]
            metadata["specialist_behavior_profile"] = profile
            metadata["specialist_behavior_source"] = source
        player = PlayerSnapshot(
            player_id=player_id,
            name=name,
            position=row["position"],
            ratings=ratings_by_player[player_id],
            role_scores=role_by_player[player_id],
            metadata=metadata,
        )
        players_by_id[player.player_id] = player
        roster.append(player)

    depth: dict[str, list[PlayerSnapshot]] = defaultdict(list)
    depth_rows = con.execute(
        """
        SELECT *
        FROM depth_charts
        WHERE team_id = ?
        ORDER BY position, depth_rank
        """,
        (team_id,),
    ).fetchall()
    for row in depth_rows:
        player = players_by_id.get(int(row["player_id"]))
        if player:
            depth[row["position"].upper()].append(player)

    return TeamSnapshot(
        team_id=int(team_row["team_id"]),
        abbreviation=team_row["abbreviation"],
        city=team_row["city"],
        nickname=team_row["nickname"],
        conference=team_row["conference"],
        division=team_row["division"],
        roster=roster,
        depth=dict(depth),
        wins=int(record_row["wins"] or 0) if record_row else 0,
        losses=int(record_row["losses"] or 0) if record_row else 0,
        ties=int(record_row["ties"] or 0) if record_row else 0,
        point_diff=int((record_row["points_for"] or 0) - (record_row["points_against"] or 0)) if record_row else 0,
    )


class MatchEngine:
    def __init__(
        self,
        *,
        away: TeamSnapshot,
        home: TeamSnapshot,
        season: int,
        week: int | None,
        schedule_game_id: int | None,
        seed: int | None = None,
    ) -> None:
        self.away = away
        self.home = home
        self.season = season
        self.week = week
        self.schedule_game_id = schedule_game_id
        self.seed = int(seed if seed is not None else random.randrange(1, 2**31))
        self.rng = random.Random(self.seed)
        self.score = {away.team_id: 0, home.team_id: 0}
        self.team_stats: dict[int, Counter] = defaultdict(Counter)
        self.player_stats: dict[int, Counter] = defaultdict(Counter)
        self.plays: list[PlayEvent] = []
        self.drives: list[DriveRecord] = []
        self.quarter = 1
        self.clock_tenths = REGULATION_QUARTER_TENTHS
        self.play_number = 0
        self.drive_number = 0
        self.first_half_receiver = self.rng.choice([away, home])
        self.second_half_receiver = home if self.first_half_receiver.team_id == away.team_id else away
        self.ot_first_drive_team_id: int | None = None
        self.ot_possessions: set[int] = set()
        self.timeouts = {away.team_id: 3, home.team_id: 3}
        self.two_minute_warnings: set[int] = set()
        self._last_play_concept: str | None = None
        self._snap_overrides: dict[tuple[int, str], list[PlayerSnapshot]] = {}
        self.injury_events: list[injury_model.InjuryEvent] = []
        self.injured_player_ids: set[int] = set()

    def opponent(self, team: TeamSnapshot) -> TeamSnapshot:
        return self.home if team.team_id == self.away.team_id else self.away

    def team_by_id(self, team_id: int) -> TeamSnapshot:
        return self.away if team_id == self.away.team_id else self.home

    def current_score_diff(self, offense: TeamSnapshot) -> int:
        return self.score[offense.team_id] - self.score[self.opponent(offense).team_id]

    def add_score(self, team: TeamSnapshot, points: int) -> None:
        self.score[team.team_id] += points
        self.team_stats[team.team_id]["points"] += points

    def add_play_event(self, event: PlayEvent) -> None:
        self.plays.append(event)

    def injury_snap_load(self, player: PlayerSnapshot) -> float:
        return float(self.player_stats[player.player_id].get("total_snaps", 0))

    def consider_injury(
        self,
        player: PlayerSnapshot | None,
        team: TeamSnapshot,
        *,
        opponent_player: PlayerSnapshot | None = None,
        opponent_team: TeamSnapshot | None = None,
        play_type: str,
        mechanism: str,
        high_impact: bool = False,
    ) -> None:
        if player is None or player.player_id in self.injured_player_ids:
            return
        event = injury_model.maybe_create_injury_event(
            self.rng,
            player,
            team_id=team.team_id,
            opponent_player=opponent_player,
            opponent_team_id=opponent_team.team_id if opponent_team else None,
            play_number=self.play_number,
            quarter=self.quarter,
            clock_tenths=self.clock_tenths,
            mechanism=mechanism,
            play_type=play_type,
            high_impact=high_impact,
            snap_load=self.injury_snap_load(player),
        )
        if not event:
            return
        self.injury_events.append(event)
        self.injured_player_ids.add(player.player_id)
        self.team_stats[team.team_id]["injuries"] += 1
        self.player_stats[player.player_id]["injury_events"] += 1
        self.player_stats[player.player_id]["injury_expected_games"] += event.expected_games

    def select_special_teams_coverage_player(self, team: TeamSnapshot, play_type: str) -> PlayerSnapshot | None:
        players = self.special_teams_snap_players(team, play_type)
        if not players:
            return None
        return weighted_choice(self.rng, [(player, special_teams_coverage_weight(player)) for player in players])

    def record_play_injuries(
        self,
        *,
        offense: TeamSnapshot,
        defense: TeamSnapshot,
        play_type: str,
        concept: str,
        yards: int,
        offense_player: PlayerSnapshot | None,
        target_player: PlayerSnapshot | None,
        defense_player: PlayerSnapshot | None,
        touchdown: bool,
        turnover: bool,
    ) -> None:
        if concept in {"kneel", "spike"} or play_type == "penalty":
            return
        high_impact = turnover or touchdown or yards >= 18 or yards <= -5
        if play_type == "run":
            mechanism = "contact" if defense_player else "non_contact"
            self.consider_injury(
                offense_player,
                offense,
                opponent_player=defense_player,
                opponent_team=defense if defense_player else None,
                play_type=play_type,
                mechanism=mechanism,
                high_impact=high_impact,
            )
            if defense_player:
                self.consider_injury(
                    defense_player,
                    defense,
                    opponent_player=offense_player,
                    opponent_team=offense if offense_player else None,
                    play_type=play_type,
                    mechanism="contact",
                    high_impact=high_impact and yards >= 8,
                )
            return
        if play_type == "pass":
            if defense_player and yards < 0:
                self.consider_injury(
                    offense_player,
                    offense,
                    opponent_player=defense_player,
                    opponent_team=defense,
                    play_type=play_type,
                    mechanism="sack",
                    high_impact=True,
                )
                self.consider_injury(
                    defense_player,
                    defense,
                    opponent_player=offense_player,
                    opponent_team=offense if offense_player else None,
                    play_type=play_type,
                    mechanism="contact",
                    high_impact=False,
                )
                return
            receiver = target_player if target_player and target_player.player_id != (offense_player.player_id if offense_player else None) else None
            if receiver and yards > 0:
                self.consider_injury(
                    receiver,
                    offense,
                    opponent_player=defense_player,
                    opponent_team=defense if defense_player else None,
                    play_type=play_type,
                    mechanism="contact" if defense_player else "non_contact",
                    high_impact=high_impact,
                )
                if defense_player:
                    self.consider_injury(
                        defense_player,
                        defense,
                        opponent_player=receiver,
                        opponent_team=offense,
                        play_type=play_type,
                        mechanism="contact",
                        high_impact=high_impact and yards >= 12,
                    )
            elif receiver:
                self.consider_injury(
                    receiver,
                    offense,
                    opponent_player=defense_player,
                    opponent_team=defense if defense_player else None,
                    play_type=play_type,
                    mechanism="non_contact",
                    high_impact=False,
                )
            return
        if play_type == "punt" and defense_player:
            cover_player = self.select_special_teams_coverage_player(offense, "punt")
            self.consider_injury(
                defense_player,
                defense,
                opponent_player=cover_player,
                opponent_team=offense if cover_player else None,
                play_type=play_type,
                mechanism="special_teams",
                high_impact=touchdown or yards >= 18,
            )

    def add_snap(self, player: PlayerSnapshot | None, snap_key: str) -> None:
        if not player:
            return
        self.player_stats[player.player_id][snap_key] += 1
        self.player_stats[player.player_id]["total_snaps"] += 1

    def add_snaps(self, players: list[PlayerSnapshot], snap_key: str) -> None:
        seen: set[int] = set()
        for player in players:
            if player.player_id in seen:
                continue
            seen.add(player.player_id)
            self.add_snap(player, snap_key)

    def eligible_slot_candidates(self, team: TeamSnapshot, slot: str, used: set[int] | None = None) -> list[PlayerSnapshot]:
        used = used or set()
        raw_candidates = list(team.candidates(slot))
        active_count = len(
            [
                player
                for player in raw_candidates
                if player.player_id not in used and player.player_id not in self.injured_player_ids
            ]
        )
        if active_count < 4:
            fallback_positions = SLOT_POSITION_FALLBACKS.get(slot.upper(), [slot.upper()])
            seen = {player.player_id for player in raw_candidates}
            supplemental = sorted(
                [player for player in team.roster if player.position in fallback_positions and player.player_id not in seen],
                key=lambda player: team.score_for_slot(player, slot),
                reverse=True,
            )
            raw_candidates.extend(supplemental)
        return [
            player
            for player in raw_candidates
            if player.player_id not in used and player.player_id not in self.injured_player_ids
        ]

    def active_starter(self, team: TeamSnapshot, slot: str) -> PlayerSnapshot:
        candidates = self.eligible_slot_candidates(team, slot)
        if candidates:
            if slot.upper() == "QB":
                return self.resolve_qb_starter(team, candidates)
            return candidates[0]
        return team.starter(slot)

    def rotation_fatigue_pressure(self, player: PlayerSnapshot, slot: str, snap_key: str) -> float:
        threshold = ROTATION_FATIGUE_THRESHOLDS.get(slot, 44)
        stamina_buffer = (player.rating("stamina") - 65.0) * 0.30
        effective_threshold = threshold + stamina_buffer
        snaps = float(self.player_stats[player.player_id].get(snap_key, 0))
        if snaps <= effective_threshold:
            return 0.0
        return clamp((snaps - effective_threshold) * 0.0065, 0.0, 0.28)

    def qb_stat_value(self, player: PlayerSnapshot, key: str) -> float:
        stats = player.metadata.get("season_stats")
        if not isinstance(stats, dict):
            return 0.0
        return float(stats.get(key, 0.0) or 0.0)

    def qb_struggle_score(self, player: PlayerSnapshot) -> float:
        attempts = self.qb_stat_value(player, "pass_attempts")
        if attempts < 90:
            return 0.0
        completions = self.qb_stat_value(player, "pass_completions")
        yards = self.qb_stat_value(player, "pass_yards")
        tds = self.qb_stat_value(player, "pass_tds")
        interceptions = self.qb_stat_value(player, "interceptions_thrown")
        sacks = self.qb_stat_value(player, "sacks_taken")
        completion_rate = completions / max(1.0, attempts)
        yards_per_attempt = yards / max(1.0, attempts)
        td_rate = tds / max(1.0, attempts)
        int_rate = interceptions / max(1.0, attempts)
        sack_rate = sacks / max(1.0, attempts + sacks)
        struggle = 0.0
        struggle += clamp((0.610 - completion_rate) / 0.090, 0.0, 1.0) * 0.22
        struggle += clamp((6.35 - yards_per_attempt) / 1.50, 0.0, 1.0) * 0.20
        struggle += clamp((0.035 - td_rate) / 0.025, 0.0, 1.0) * 0.16
        struggle += clamp((int_rate - 0.026) / 0.030, 0.0, 1.0) * 0.24
        struggle += clamp((sack_rate - 0.082) / 0.070, 0.0, 1.0) * 0.18
        if attempts >= 180:
            struggle += 0.07
        return clamp(struggle, 0.0, 1.0)

    def qb_development_investment(self, player: PlayerSnapshot) -> float:
        age = int(player.metadata.get("age") or 26)
        is_rookie = bool(int(player.metadata.get("is_rookie") or 0))
        years_exp = int(player.metadata.get("years_exp") or 0)
        if not is_rookie and not (age <= 24 and years_exp <= 2):
            return 0.0
        overall = float(player.metadata.get("overall") or player.general_score())
        potential = float(player.metadata.get("potential") or overall)
        draft_round = player.metadata.get("draft_round")
        draft_pick = player.metadata.get("draft_pick_number")
        dev_trait = str(player.metadata.get("dev_trait") or "Normal")
        investment = 0.18 if is_rookie else 0.08
        if draft_round == 1:
            investment += 0.42
            if isinstance(draft_pick, int) and draft_pick <= 16:
                investment += 0.12
        elif draft_round == 2:
            investment += 0.24
        elif draft_round == 3:
            investment += 0.12
        investment += clamp((potential - overall) / 18.0, 0.0, 0.24)
        investment += clamp((potential - 78.0) / 22.0, 0.0, 0.16)
        if dev_trait in {"Star", "Impact"}:
            investment += 0.08
        elif dev_trait in {"Superstar", "Elite", "X-Factor"}:
            investment += 0.14
        return clamp(investment, 0.0, 1.0)

    def resolve_qb_starter(self, team: TeamSnapshot, candidates: list[PlayerSnapshot]) -> PlayerSnapshot:
        starter = candidates[0]
        if len(candidates) == 1:
            return starter
        starter_score = team.score_for_slot(starter, "QB")
        starter_struggle = self.qb_struggle_score(starter)
        week = int(self.week or team.games_played() + 1 or 1)
        win_pct = team.win_pct()
        point_diff_per_game = team.point_diff / max(1, team.games_played())
        losing_context = win_pct < 0.45 or point_diff_per_game < -3.5
        contention_protection = win_pct >= 0.60 and point_diff_per_game >= 1.0 and starter_struggle < 0.42

        best_candidate = starter
        best_case = 0.0
        for candidate in candidates[1:4]:
            if candidate.player_id in self.injured_player_ids:
                continue
            investment = self.qb_development_investment(candidate)
            if investment <= 0:
                continue
            candidate_score = team.score_for_slot(candidate, "QB")
            quality_gap = starter_score - candidate_score
            overall = float(candidate.metadata.get("overall") or candidate_score)
            potential = float(candidate.metadata.get("potential") or overall)
            draft_round = candidate.metadata.get("draft_round")
            high_investment = draft_round == 1 or investment >= 0.54
            raw = quality_gap > 6.0 or overall < 68
            readiness = clamp((candidate_score - 62.0) / 18.0, 0.0, 1.0)
            case = investment * 0.38 + readiness * 0.22 + starter_struggle * 0.30
            if losing_context:
                case += 0.16
            if week >= 7 and high_investment:
                case += 0.13
            if week >= 10 and (losing_context or starter_struggle >= 0.38):
                case += 0.14
            if week >= 13 and win_pct < 0.50:
                case += 0.14
            if potential >= 84:
                case += 0.05
            case -= max(0.0, quality_gap - 2.0) * (0.030 if raw else 0.018)
            if contention_protection:
                case -= 0.24
            if high_investment and quality_gap <= 2.0 and overall >= 70:
                case += 0.18
            threshold = 0.68
            if week <= 4 and not (quality_gap <= 1.0 and overall >= 71):
                threshold += 0.18
            if raw and week < 8:
                threshold += 0.16
            if quality_gap > 10.0:
                threshold += 0.22
            if case >= threshold and case > best_case:
                best_candidate = candidate
                best_case = case
        return best_candidate

    def choose_rotated_slot_player(
        self,
        team: TeamSnapshot,
        slot: str,
        *,
        used: set[int],
        snap_key: str,
        starter_rate: float | None = None,
        preferred: PlayerSnapshot | None = None,
    ) -> PlayerSnapshot | None:
        if (
            preferred
            and preferred.player_id not in used
            and preferred.player_id not in self.injured_player_ids
            and preferred in team.roster
        ):
            return preferred
        candidates = self.eligible_slot_candidates(team, slot, used)
        if not candidates:
            return None
        if len(candidates) == 1:
            return candidates[0]

        base_rate = starter_rate
        if base_rate is None:
            base_rate = OFFENSIVE_SLOT_STARTER_RATE.get(slot, DEFENSIVE_SLOT_STARTER_RATE.get(slot, 0.84))
        starter = candidates[0]
        if slot == "QB":
            return self.resolve_qb_starter(team, candidates)
        if slot in {"QB", "LT", "LG", "C", "RG", "RT"}:
            return starter
        starter_score = team.score_for_slot(starter, slot)
        evaluation_mode = self.youth_evaluation_mode_score(team)
        if slot in {"LWR", "RWR", "SWR"}:
            if starter_score >= 90:
                base_rate = max(base_rate, 0.95)
            elif starter_score >= 88:
                base_rate = max(base_rate, 0.92)
            elif starter_score >= 82:
                base_rate = max(base_rate, 0.88 if slot != "SWR" else 0.83)
        elif slot == "TE" and starter_score >= 82:
            base_rate = max(base_rate, 0.84)
        elif slot == "RB":
            if starter_score >= 86:
                base_rate = max(base_rate, 0.82)
            elif starter_score >= 82:
                base_rate = max(base_rate, 0.78)
            elif starter_score >= 78:
                base_rate = max(base_rate, 0.74)
            else:
                base_rate = max(base_rate, 0.70)
        backup_score = team.score_for_slot(candidates[1], slot)
        quality_gap = starter_score - backup_score
        if slot == "RB":
            backup_overall = float(candidates[1].metadata.get("overall") or candidates[1].general_score())
            if quality_gap >= 9.0:
                base_rate = max(base_rate, 0.82)
            elif quality_gap >= 6.0:
                base_rate = max(base_rate, 0.78)
            if backup_overall < 70 and quality_gap >= 4.0:
                base_rate = max(base_rate, 0.80)
        developmental_pressure = self.developmental_rotation_pressure(
            team,
            slot,
            starter_score=starter_score,
            candidates=candidates[1:5],
            evaluation_mode=evaluation_mode,
        )
        stamina_bonus = (starter.rating("stamina") - 65.0) * 0.0017
        quality_bonus = clamp(quality_gap * 0.006, -0.055, 0.095)
        fatigue_penalty = self.rotation_fatigue_pressure(starter, slot, snap_key)
        evaluation_floor = 0.18 + evaluation_mode * 0.11 if evaluation_mode > 0 else 0.24
        if slot == "RB":
            evaluation_floor = 0.46 if evaluation_mode > 0 else 0.54
        effective_rate = clamp(
            base_rate + quality_bonus + stamina_bonus - fatigue_penalty - developmental_pressure,
            evaluation_floor,
            0.998,
        )
        if self.rng.random() < effective_rate:
            return starter

        starter_snaps = float(self.player_stats[starter.player_id].get(snap_key, 0))
        weights = []
        for idx, backup in enumerate(candidates[1:5]):
            score = max(1.0, team.score_for_slot(backup, slot) - 45.0)
            depth_discount = 1.0 / (idx + 1.35)
            freshness = 1.0 + clamp((starter_snaps - float(self.player_stats[backup.player_id].get(snap_key, 0))) * 0.014, 0.0, 0.45)
            stamina = 1.0 + clamp((backup.rating("stamina") - 62.0) * 0.004, -0.08, 0.12)
            development = self.developmental_rotation_weight(
                slot,
                starter_score=starter_score,
                player_score=team.score_for_slot(backup, slot),
                player=backup,
                evaluation_mode=evaluation_mode,
            )
            weights.append((backup, score * depth_discount * freshness * stamina * development))
        return weighted_choice(self.rng, weights) if weights else starter

    def youth_evaluation_mode_score(self, team: TeamSnapshot) -> float:
        games = team.games_played()
        if games < 6:
            return 0.0
        week = int(self.week or games + 1 or 1)
        win_pct = team.win_pct()
        point_diff = team.point_diff_per_game()
        score = 0.0
        if week >= 8 and (win_pct < 0.35 or point_diff < -7.0):
            score = max(score, 0.42)
        if week >= 11 and (win_pct < 0.45 or point_diff < -4.0):
            score = max(score, 0.62)
        if week >= 14 and win_pct < 0.50:
            score = max(score, 0.78)
        if week >= 16 and win_pct < 0.56 and point_diff < -1.5:
            score = max(score, 0.92)
        return clamp(score, 0.0, 1.0)

    def developmental_rotation_pressure(
        self,
        team: TeamSnapshot,
        slot: str,
        *,
        starter_score: float,
        candidates: list[PlayerSnapshot],
        evaluation_mode: float = 0.0,
    ) -> float:
        if slot not in DEVELOPMENTAL_ROTATION_SLOTS:
            return 0.0
        best = 0.0
        for player in candidates:
            score = team.score_for_slot(player, slot)
            best = max(
                best,
                self.developmental_rotation_weight(
                    slot,
                    starter_score=starter_score,
                    player_score=score,
                    player=player,
                    evaluation_mode=evaluation_mode,
                )
                - 1.0,
            )
        if best <= 0:
            return 0.0
        pressure = best * (0.045 + evaluation_mode * 0.120)
        if slot == "RB":
            pressure *= 0.42
        if starter_score >= 88 and evaluation_mode < 0.85:
            pressure *= 0.35
        elif starter_score >= 84 and evaluation_mode < 0.55:
            pressure *= 0.62
        return clamp(pressure, 0.0, 0.12 if slot == "RB" else 0.32)

    def developmental_rotation_weight(
        self,
        slot: str,
        *,
        starter_score: float,
        player_score: float,
        player: PlayerSnapshot,
        evaluation_mode: float = 0.0,
    ) -> float:
        if slot not in DEVELOPMENTAL_ROTATION_SLOTS:
            return 1.0
        age = int(player.metadata.get("age") or 26)
        is_rookie = bool(int(player.metadata.get("is_rookie") or 0))
        years_exp = int(player.metadata.get("years_exp") or 0)
        if not is_rookie and not (age <= 23 and years_exp <= 2):
            return 1.0
        quality_gap = starter_score - player_score
        max_gap = 13.0 + evaluation_mode * 5.0
        if slot == "RB":
            max_gap = 8.0 + evaluation_mode * 2.5
        if quality_gap > max_gap:
            return 1.0
        overall = float(player.metadata.get("overall") or player.general_score())
        potential = float(player.metadata.get("potential") or overall)
        if slot == "RB" and overall < 68 and potential < 76:
            return 1.0
        potential_gap = max(0.0, potential - overall)
        dev_trait = str(player.metadata.get("dev_trait") or "Normal")
        upside = clamp(potential_gap / 12.0, 0.0, 1.0)
        closeness = clamp((max_gap - max(0.0, quality_gap)) / max_gap, 0.0, 1.0)
        trait_bonus = 0.0
        if dev_trait in {"Star", "Impact"}:
            trait_bonus = 0.10
        elif dev_trait in {"Superstar", "Elite", "X-Factor"}:
            trait_bonus = 0.18
        slot_bonus = 0.08 if slot in {"TE", "SWR", "NB", "LEDGE", "REDGE", "LDL", "RDL", "NT"} else 0.03
        if slot == "RB":
            slot_bonus = 0.02
        rookie_bonus = 0.12 if is_rookie else 0.04
        evaluation_bonus = evaluation_mode * (0.18 + closeness * 0.18 + upside * 0.14)
        if slot == "RB":
            rookie_bonus *= 0.60
            evaluation_bonus *= 0.55
        return clamp(
            1.0 + rookie_bonus + slot_bonus + upside * 0.34 + closeness * 0.28 + trait_bonus + evaluation_bonus,
            1.0,
            1.55 if slot == "RB" else 2.20,
        )

    def append_rotated_slot(
        self,
        players: list[PlayerSnapshot],
        team: TeamSnapshot,
        slot: str,
        used: set[int],
        *,
        snap_key: str,
        preferred: PlayerSnapshot | None = None,
        starter_rate: float | None = None,
    ) -> None:
        player = self.choose_rotated_slot_player(
            team,
            slot,
            used=used,
            snap_key=snap_key,
            starter_rate=starter_rate,
            preferred=preferred,
        )
        if not player:
            return
        players.append(player)
        used.add(player.player_id)

    def offensive_skill_slots(self, offense: TeamSnapshot, concept: str) -> list[str]:
        fullback = offense.starter("FB")
        has_fullback = fullback and fullback.position == "FB"
        if concept in {"inside_zone", "power"}:
            if has_fullback and self.rng.random() < 0.55:
                return ["TE", "FB", "LWR", "RWR"]
            if self.rng.random() < 0.32:
                return ["TE", "TE", "LWR", "RWR"]
        if concept == "screen" and self.rng.random() < 0.22:
            return ["LWR", "RWR", "SWR", "SWR"]
        return ["TE", "LWR", "RWR", "SWR"]

    def offensive_snap_players(
        self,
        offense: TeamSnapshot,
        concept: str,
        play_type: str = "",
        *,
        ball_carrier: PlayerSnapshot | None = None,
        target: PlayerSnapshot | None = None,
    ) -> list[PlayerSnapshot]:
        players: list[PlayerSnapshot] = []
        used: set[int] = set()
        self.append_rotated_slot(players, offense, "QB", used, snap_key="offensive_snaps")
        for slot in ["LT", "LG", "C", "RG", "RT"]:
            self.append_rotated_slot(players, offense, slot, used, snap_key="offensive_snaps")

        rb_preferred = None
        rb_starter_rate = None
        if ball_carrier and ball_carrier.position in {"RB", "FB"}:
            rb_preferred = ball_carrier
        elif target and target.position == "RB":
            rb_preferred = target
        elif play_type == "pass":
            if concept == "screen":
                rb_starter_rate = 0.52
            elif concept in {"quick", "short"}:
                rb_starter_rate = 0.60
            else:
                rb_starter_rate = 0.68
        self.append_rotated_slot(
            players,
            offense,
            "RB",
            used,
            snap_key="offensive_snaps",
            preferred=rb_preferred,
            starter_rate=rb_starter_rate,
        )

        force_skill = None
        if target and target.position in {"WR", "TE", "FB"}:
            force_skill = target
        for slot in self.offensive_skill_slots(offense, concept):
            preferred = None
            if force_skill and force_skill.player_id not in used:
                slot_positions = SLOT_POSITION_FALLBACKS.get(slot, [slot])
                if force_skill.position in slot_positions:
                    preferred = force_skill
            self.append_rotated_slot(players, offense, slot, used, snap_key="offensive_snaps", preferred=preferred)

        feature_receivers = sorted(
            [
                player
                for player in offense.roster
                if player.position == "WR" and player.player_id not in self.injured_player_ids
            ],
            key=lambda player: max(offense.score_for_slot(player, slot) for slot in ("LWR", "RWR", "SWR")),
            reverse=True,
        )[:2]
        for receiver in feature_receivers:
            if receiver.player_id in used:
                continue
            receiver_score = max(offense.score_for_slot(receiver, slot) for slot in ("LWR", "RWR", "SWR"))
            if receiver_score < 84:
                continue
            wr_indexes = [
                (idx, player, max(offense.score_for_slot(player, slot) for slot in ("LWR", "RWR", "SWR")))
                for idx, player in enumerate(players)
                if player.position == "WR"
            ]
            if not wr_indexes:
                continue
            replace_idx, replaced, replaced_score = min(wr_indexes, key=lambda item: item[2])
            if receiver_score - replaced_score < 6:
                continue
            players[replace_idx] = receiver
            used.discard(replaced.player_id)
            used.add(receiver.player_id)

        if target and target.player_id not in used and target.player_id not in self.injured_player_ids:
            players.append(target)
            used.add(target.player_id)
        fill_slots = ["SWR", "TE", "LWR", "RWR", "FB", "RB"]
        while len(players) < 11:
            before = len(players)
            for slot in fill_slots:
                self.append_rotated_slot(players, offense, slot, used, snap_key="offensive_snaps")
                if len(players) >= 11:
                    break
            if len(players) == before:
                break
        return players[:11]

    def rotated_unique_starters(self, team: TeamSnapshot, slots: list[str]) -> list[PlayerSnapshot]:
        selected = []
        used = set()
        for slot in slots:
            candidates = self.eligible_slot_candidates(team, slot, used)
            primary = candidates[0] if candidates else None
            player = self.choose_rotated_slot_player(
                team,
                slot,
                used=used,
                snap_key="defensive_snaps",
                starter_rate=DEFENSIVE_SLOT_STARTER_RATE.get(slot, 0.90),
            )
            if not player:
                continue
            selected.append(player)
            used.add(player.player_id)
            if primary and primary.player_id != player.player_id:
                used.add(primary.player_id)
        return selected

    def defensive_snap_players(self, defense: TeamSnapshot, play_type: str, concept: str) -> list[PlayerSnapshot]:
        if play_type == "pass":
            slots = ["LEDGE", "LDL", "RDL", "REDGE", "MLB", "WLB", "LCB", "RCB", "NB", "FS", "SS"]
        elif concept in {"inside_zone", "power"}:
            slots = ["LEDGE", "LDL", "NT", "RDL", "REDGE", "MLB", "WLB", "SLB", "LCB", "RCB", "SS"]
        else:
            slots = ["LEDGE", "LDL", "NT", "REDGE", "MLB", "WLB", "LCB", "RCB", "NB", "FS", "SS"]
        return self.rotated_unique_starters(defense, slots)

    def pass_rushers_from_snap(self, defense: TeamSnapshot, defenders: list[PlayerSnapshot]) -> list[PlayerSnapshot]:
        front = defenders[:4] if len(defenders) >= 4 else defenders
        rushers = [player for player in front if player.position in {"EDGE", "OLB", "DE", "IDL", "DT", "NT"}]
        return rushers or defense.defensive_front()

    def set_snap_override(self, team: TeamSnapshot, snap_key: str, players: list[PlayerSnapshot]) -> None:
        self._snap_overrides[(team.team_id, snap_key)] = players

    def special_teams_role_score(self, team: TeamSnapshot, player: PlayerSnapshot, play_type: str) -> float:
        if play_type in {"field_goal", "extra_point"}:
            base = special_teams_block_weight(player)
            prior = SPECIAL_TEAMS_BLOCKER_PRIOR.get(player.position, 0.72)
        elif play_type in {"kick_return", "punt_return"}:
            base = max(special_teams_block_weight(player), special_teams_coverage_weight(player) * 0.85)
            prior = SPECIAL_TEAMS_POSITION_PRIOR.get(player.position, 0.86)
        else:
            base = special_teams_coverage_weight(player)
            prior = SPECIAL_TEAMS_POSITION_PRIOR.get(player.position, 0.82)

        depth_rank = team.depth_rank_for_player(player)
        if depth_rank == 1:
            depth_factor = 0.58 if player.general_score() >= 76 else 0.74
        elif depth_rank == 2:
            depth_factor = 1.20
        elif depth_rank == 3:
            depth_factor = 1.15
        elif depth_rank in {4, 5}:
            depth_factor = 1.04
        elif depth_rank is None:
            depth_factor = 0.92
        else:
            depth_factor = 0.84

        roster_value_factor = 1.0
        general = player.general_score()
        if general >= 86:
            roster_value_factor *= 0.28
        elif general >= 80:
            roster_value_factor *= 0.45
        elif general >= 76:
            roster_value_factor *= 0.65
        elif general < 60:
            roster_value_factor *= 0.88

        current_st_snaps = float(self.player_stats[player.player_id].get("special_teams_snaps", 0))
        load_factor = clamp(1.20 - current_st_snaps * 0.022, 0.54, 1.20)
        source = str(player.metadata.get("specialist_behavior_source") or "")
        profile_factor = 1.10 if source.startswith("specialist_behavior_") or source == "draft_selection" else 1.0
        age = int(player.metadata.get("age") or 26)
        is_rookie = bool(int(player.metadata.get("is_rookie") or 0))
        potential = float(player.metadata.get("potential") or player.general_score())
        overall = float(player.metadata.get("overall") or player.general_score())
        development_factor = 1.0
        if is_rookie or age <= 23:
            development_factor += 0.08 + clamp((potential - overall) / 20.0, 0.0, 0.12)
        return max(0.05, base * prior * depth_factor * roster_value_factor * load_factor * profile_factor * development_factor)

    def core_special_teamers(
        self,
        team: TeamSnapshot,
        count: int,
        *,
        play_type: str = "kickoff",
        exclude_player_ids: set[int] | None = None,
    ) -> list[PlayerSnapshot]:
        exclude_player_ids = set(exclude_player_ids or set())
        pool = []
        for player in team.roster:
            if player.player_id in exclude_player_ids or player.position in {"K", "P", "QB", "OT", "OG", "C", "OL"}:
                continue
            if player.player_id in self.injured_player_ids:
                continue
            source = str(player.metadata.get("specialist_behavior_source") or "")
            has_stored_st_profile = source.startswith("specialist_behavior_") or source == "draft_selection"
            if not has_stored_st_profile and player.general_score() > 76:
                continue
            pool.append(player)
        weighted_pool = sorted(
            ((player, self.special_teams_role_score(team, player, play_type)) for player in pool),
            key=lambda item: item[1],
            reverse=True,
        )
        return [
            player
            for player, _weight in weighted_pool[:count]
        ]

    def special_teams_snap_players(self, team: TeamSnapshot, play_type: str) -> list[PlayerSnapshot]:
        if play_type in {"field_goal", "extra_point"}:
            players = team.unique_starters(["PK", "LS", "PT"])
            players.extend(
                self.core_special_teamers(
                    team,
                    8,
                    play_type=play_type,
                    exclude_player_ids={player.player_id for player in players},
                )
            )
            return players
        if play_type == "kickoff":
            players = team.unique_starters(["KO"])
            players.extend(self.core_special_teamers(team, 10, play_type=play_type, exclude_player_ids={player.player_id for player in players}))
            return players
        if play_type == "safety_kick":
            players = team.unique_starters(["PT"])
            players.extend(self.core_special_teamers(team, 10, play_type=play_type, exclude_player_ids={player.player_id for player in players}))
            return players
        if play_type == "kick_return":
            returner = self.select_returner(team, "kickoff")
            players = [returner] if returner else []
            players.extend(self.core_special_teamers(team, 10, play_type=play_type, exclude_player_ids={player.player_id for player in players}))
            return players
        if play_type == "punt":
            players = team.unique_starters(["PT", "LS"])
            players.extend(self.core_special_teamers(team, 9, play_type=play_type, exclude_player_ids={player.player_id for player in players}))
            return players
        if play_type == "punt_return":
            returner = self.select_returner(team, "punt")
            players = [returner] if returner else []
            players.extend(self.core_special_teamers(team, 10, play_type=play_type, exclude_player_ids={player.player_id for player in players}))
            return players
        return []

    def count_scrimmage_snap(
        self,
        offense: TeamSnapshot,
        defense: TeamSnapshot,
        play_type: str,
        concept: str,
        *,
        offense_player: PlayerSnapshot | None = None,
        target_player: PlayerSnapshot | None = None,
    ) -> None:
        offense_players = self._snap_overrides.pop((offense.team_id, "offensive_snaps"), None)
        defense_players = self._snap_overrides.pop((defense.team_id, "defensive_snaps"), None)
        if offense_players is not None:
            forced = [
                player
                for player in (offense_player, target_player)
                if player and player.player_id not in self.injured_player_ids
            ]
            offense_players = [*offense_players, *forced]
        self.add_snaps(offense_players or self.offensive_snap_players(offense, concept), "offensive_snaps")
        self.add_snaps(defense_players or self.defensive_snap_players(defense, play_type, concept), "defensive_snaps")

    def count_special_teams_snap(self, team: TeamSnapshot, play_type: str) -> None:
        self.add_snaps(self.special_teams_snap_players(team, play_type), "special_teams_snaps")

    def count_special_teams_play(self, kicking_team: TeamSnapshot, receiving_team: TeamSnapshot, play_type: str) -> None:
        self.count_special_teams_snap(kicking_team, play_type)
        if play_type in {"kickoff", "safety_kick"}:
            self.count_special_teams_snap(receiving_team, "kick_return")
        elif play_type == "punt":
            self.count_special_teams_snap(receiving_team, "punt_return")

    def kicking_operation_score(self, team: TeamSnapshot, play_type: str) -> float:
        if play_type in {"field_goal", "extra_point"}:
            kicker = team.starter("PK")
            long_snapper = team.starter("LS")
            holder = team.starter("PT")
            if kicker.position not in KICKER_POSITIONS:
                return 35.0
            kicker_profile = specialist_behavior_profile(kicker)
            snap_profile = specialist_behavior_profile(long_snapper)
            holder_profile = specialist_behavior_profile(holder)
            return (
                kicker_profile.kick_operation * 0.58
                + snap_profile.snap_accuracy * 0.28
                + holder_profile.punt_placement * 0.08
                + average([kicker_profile.penalty_control, snap_profile.penalty_control]) * 0.06
            )
        if play_type == "punt":
            punter = team.starter("PT")
            long_snapper = team.starter("LS")
            if punter.position not in PUNTER_POSITIONS:
                return 38.0
            punter_profile = specialist_behavior_profile(punter)
            snap_profile = specialist_behavior_profile(long_snapper)
            return (
                punter_profile.punt_hang_time * 0.34
                + punter_profile.punt_placement * 0.34
                + snap_profile.snap_accuracy * 0.22
                + average([punter_profile.penalty_control, snap_profile.penalty_control]) * 0.10
            )
        return team.discipline_score()

    def special_teams_coverage_score(self, team: TeamSnapshot, play_type: str) -> float:
        players = self.special_teams_snap_players(team, play_type)
        if not players:
            return team.tackling_score()
        return average(special_teams_coverage_weight(player) for player in players)

    def special_teams_block_score(self, team: TeamSnapshot, play_type: str) -> float:
        players = self.special_teams_snap_players(team, play_type)
        if not players:
            return team.discipline_score()
        return average(special_teams_block_weight(player) for player in players)

    def select_special_teams_blocker(self, team: TeamSnapshot, play_type: str) -> PlayerSnapshot:
        pool = self.special_teams_snap_players(team, play_type) or team.roster[:11]
        return weighted_choice(self.rng, [(player, special_teams_block_weight(player)) for player in pool])

    def consume_clock(self, live_tenths: int, runoff_tenths: int) -> tuple[int, int]:
        live_tenths = max(1, int(live_tenths))
        runoff_tenths = max(0, int(runoff_tenths))
        total = live_tenths + runoff_tenths
        warning_tenths = 2 * 60 * TENTHS_PER_SECOND
        if self.quarter in {2, 4} and self.quarter not in self.two_minute_warnings:
            if self.clock_tenths > warning_tenths and self.clock_tenths - total <= warning_tenths:
                consumed = self.clock_tenths - warning_tenths
                self.clock_tenths = warning_tenths
                self.two_minute_warnings.add(self.quarter)
                return consumed, max(0, consumed - live_tenths)
        consumed = min(self.clock_tenths, total)
        self.clock_tenths -= consumed
        consumed_runoff = max(0, consumed - live_tenths)
        return consumed, consumed_runoff

    def reset_half_timeouts(self) -> None:
        self.timeouts[self.away.team_id] = 3
        self.timeouts[self.home.team_id] = 3

    def reset_overtime_timeouts(self) -> None:
        self.timeouts[self.away.team_id] = 2
        self.timeouts[self.home.team_id] = 2

    def maybe_use_timeout(
        self,
        offense: TeamSnapshot,
        defense: TeamSnapshot,
        *,
        play_type: str,
        runoff_tenths: int,
        stops_clock: bool,
    ) -> tuple[int, str]:
        if runoff_tenths <= 0 or stops_clock or self.quarter not in {2, 4, 5}:
            return runoff_tenths, ""
        offense_diff = self.current_score_diff(offense)
        defense_diff = -offense_diff
        if offense_diff < 0 and self.clock_tenths <= 2 * 60 * TENTHS_PER_SECOND and self.timeouts[offense.team_id] > 0:
            self.timeouts[offense.team_id] -= 1
            self.team_stats[offense.team_id]["timeouts_used"] += 1
            return 0, f" {offense.abbreviation} timeout."
        if defense_diff < 0 and self.clock_tenths <= 5 * 60 * TENTHS_PER_SECOND and play_type == "run" and self.timeouts[defense.team_id] > 0:
            self.timeouts[defense.team_id] -= 1
            self.team_stats[defense.team_id]["timeouts_used"] += 1
            return 0, f" {defense.abbreviation} timeout."
        return runoff_tenths, ""

    def advance_dead_quarter_if_needed(self, offense: TeamSnapshot, field_pos: int, down: int, distance: int) -> tuple[bool, TeamSnapshot, int, int, int]:
        if self.clock_tenths > 0:
            return False, offense, field_pos, down, distance
        if self.quarter in (1, 3):
            self.quarter += 1
            self.clock_tenths = REGULATION_QUARTER_TENTHS
            return False, offense, field_pos, down, distance
        if self.quarter == 2:
            self.quarter = 3
            self.clock_tenths = REGULATION_QUARTER_TENTHS
            return True, self.second_half_receiver, 25, 1, 10
        return True, offense, field_pos, down, distance

    def play_call_is_pass(self, offense: TeamSnapshot, defense: TeamSnapshot, down: int, distance: int, field_pos: int) -> bool:
        pass_rate = 0.56
        if down == 1:
            pass_rate -= 0.04
        elif down == 2 and distance >= 8:
            pass_rate += 0.09
        elif down == 3 and distance >= 7:
            pass_rate += 0.24
        elif down == 3 and distance <= 3:
            pass_rate -= 0.12
        if field_pos >= 80:
            pass_rate -= 0.05
        score_diff = self.current_score_diff(offense)
        late = self.quarter >= 4 or self.quarter == 5
        if late and score_diff < 0:
            pass_rate += min(0.20, abs(score_diff) * 0.012)
        elif late and score_diff > 0:
            pass_rate -= min(0.18, score_diff * 0.010)

        qb = self.active_starter(offense, "QB")
        pass_identity = weighted_average(qb, QB_PASS_WEIGHTS)
        rb = offense.starter("RB")
        run_identity = weighted_average(rb, RB_RUN_WEIGHTS)
        pass_rate += (pass_identity - run_identity) * 0.002
        rb_profile = rb_behavior_profile(rb)
        pass_rate += (rb_profile.pass_game_usage - 50) * 0.0008
        if down <= 2 and distance <= 3:
            pass_rate -= (rb_profile.short_yardage_trust - 50) * 0.0008
        return self.rng.random() < clamp(pass_rate, 0.25, 0.86)

    def fourth_down_decision(self, offense: TeamSnapshot, defense: TeamSnapshot, down: int, distance: int, field_pos: int) -> str:
        fg_distance = 100 - field_pos + 17
        score_diff = self.current_score_diff(offense)
        late_trailing = self.quarter >= 4 and score_diff < 0
        late_half = self.quarter in {2, 4} and self.clock_tenths <= 90 * TENTHS_PER_SECOND
        kicker = offense.starter("PK")
        real_kicker = kicker.position in KICKER_POSITIONS
        kick_score = weighted_average(kicker, KICK_WEIGHTS) if real_kicker else 42.0
        operation_score = self.kicking_operation_score(offense, "field_goal")

        if not real_kicker:
            if field_pos >= 45 and distance <= 1 and self.rng.random() < 0.70:
                return "go"
            if late_trailing and field_pos >= 45 and distance <= 6 and self.rng.random() < 0.62:
                return "go"
            return "punt"

        if field_pos >= 75 and fg_distance <= 42:
            if late_trailing and distance <= 4:
                return "go"
            if distance <= 1 and self.rng.random() < 0.44:
                return "go"
            return "field_goal"

        routine_field_goal_range = field_pos >= 66 and fg_distance <= 54
        long_field_goal_range = field_pos >= 54 and fg_distance <= 63
        extreme_field_goal_range = field_pos >= 49 and fg_distance <= 68
        if routine_field_goal_range and not (late_trailing and distance <= 4 and field_pos >= 55):
            return "field_goal"

        go_prob = 0.04
        if field_pos >= 45 and distance <= 1:
            go_prob = 0.68
        elif field_pos >= 50 and distance <= 3:
            go_prob = 0.46
        elif late_trailing and field_pos >= 45 and distance <= 6:
            go_prob = 0.55
        if self.rng.random() < go_prob:
            return "go"
        if routine_field_goal_range:
            return "field_goal"
        if long_field_goal_range:
            long_try_prob = (
                0.12
                + (kick_score - 70) * 0.009
                + (operation_score - 70) * 0.003
                - max(0, fg_distance - 55) * 0.045
            )
            if late_half:
                long_try_prob += 0.18
            if late_trailing:
                long_try_prob += 0.16
            if fg_distance >= 61:
                long_try_prob += 0.05 if self.clock_tenths <= 45 * TENTHS_PER_SECOND else 0.0
                long_try_prob -= (fg_distance - 60) * 0.040
            if distance >= 8:
                long_try_prob += 0.06
            if distance <= 2:
                long_try_prob -= 0.06
            if self.rng.random() < clamp(long_try_prob, 0.02, 0.50 if late_half else 0.36):
                return "field_goal"
        if extreme_field_goal_range and late_half:
            urgency = 0.0
            if self.clock_tenths <= 8 * TENTHS_PER_SECOND:
                urgency = 0.38
            elif self.clock_tenths <= 20 * TENTHS_PER_SECOND:
                urgency = 0.25
            elif self.clock_tenths <= 45 * TENTHS_PER_SECOND:
                urgency = 0.14
            elif self.clock_tenths <= 75 * TENTHS_PER_SECOND:
                urgency = 0.07
            end_half_try = self.quarter == 2 and self.clock_tenths <= 12 * TENTHS_PER_SECOND
            must_score = late_trailing and score_diff >= -3
            last_snap = self.clock_tenths <= 6 * TENTHS_PER_SECOND
            desperation_prob = (
                0.008
                + urgency
                + (0.10 if end_half_try else 0.0)
                + (0.08 if last_snap and kick_score >= 80 else 0.0)
                + (0.12 if must_score else 0.0)
                + (kick_score - 78) * 0.008
                + (operation_score - 72) * 0.002
                - max(0, fg_distance - 60) * 0.050
            )
            if fg_distance >= 65:
                desperation_prob -= (fg_distance - 64) * 0.035
            if distance <= 2 and not (self.clock_tenths <= 12 * TENTHS_PER_SECOND or late_trailing):
                desperation_prob -= 0.10
            if self.rng.random() < clamp(desperation_prob, 0.0, 0.42):
                return "field_goal"
        return "punt"

    def should_kneel(self, offense: TeamSnapshot, defense: TeamSnapshot, down: int) -> bool:
        if self.quarter != 4 or self.current_score_diff(offense) <= 0 or down >= 4:
            return False
        if self.timeouts[defense.team_id] > 0:
            return False
        remaining_kneel_clock = (4 - down) * 42 * TENTHS_PER_SECOND
        return self.clock_tenths <= remaining_kneel_clock

    def kneel_play(self, offense: TeamSnapshot, defense: TeamSnapshot, field_pos: int) -> tuple[int, str, PlayerSnapshot]:
        qb = self.active_starter(offense, "QB")
        yards = -1 if field_pos > 1 else 0
        self.team_stats[offense.team_id]["plays"] += 1
        self.team_stats[offense.team_id]["rush_attempts"] += 1
        self.team_stats[offense.team_id]["rush_yards"] += yards
        self.team_stats[offense.team_id]["total_yards"] += yards
        self.player_stats[qb.player_id]["rush_attempts"] += 1
        self.player_stats[qb.player_id]["rush_yards"] += yards
        return yards, f"{qb.name} takes a knee.", qb

    def should_spike(self, offense: TeamSnapshot, down: int, field_pos: int) -> bool:
        if self.quarter not in {2, 4, 5} or down >= 4:
            return False
        if self.current_score_diff(offense) > 0 or self.timeouts[offense.team_id] > 0:
            return False
        return self.clock_tenths <= 38 * TENTHS_PER_SECOND and field_pos >= 45

    def spike_play(self, offense: TeamSnapshot) -> tuple[str, PlayerSnapshot]:
        qb = self.active_starter(offense, "QB")
        self.team_stats[offense.team_id]["plays"] += 1
        self.team_stats[offense.team_id]["pass_attempts"] += 1
        self.player_stats[qb.player_id]["pass_attempts"] += 1
        return f"{qb.name} spikes the ball to stop the clock.", qb

    def choose_run_concept(
        self,
        offense: TeamSnapshot,
        defense: TeamSnapshot,
        down: int,
        distance: int,
        field_pos: int,
        runner: PlayerSnapshot | None = None,
    ) -> str:
        inside = 1.10
        outside = 0.95
        power = 0.78
        draw = 0.52
        if down >= 3 and distance >= 6:
            draw += 0.28
            power -= 0.18
        if distance <= 2 or field_pos >= 85:
            power += 0.55
            draw -= 0.20
        line = offense.offensive_line_profile_summary()
        if line:
            inside += (line["combo_timing"] - 50) * 0.004 + (line["drive_finish"] - 50) * 0.003
            outside += (line["reach_range"] - 50) * 0.005 + (line["second_level_climb"] - 50) * 0.004
            power += (line["drive_finish"] - 50) * 0.006 + (line["anchor_vs_power"] - 50) * 0.003
            draw += (line["pass_set_patience"] - 50) * 0.003 + (line["stunt_awareness"] - 50) * 0.002
        if runner and runner.position in {"RB", "FB"}:
            profile = rb_behavior_profile(runner)
            inside += (profile.patience - 50) * 0.008 + (profile.one_cut_decisiveness - 50) * 0.006
            outside += (profile.bounce_tendency - 50) * 0.011 + (profile.home_run_hunting - 50) * 0.005
            power += (profile.contact_appetite - 50) * 0.010 + (profile.short_yardage_trust - 50) * 0.009
            draw += (profile.pass_game_usage - 50) * 0.006 + (profile.space_creation - 50) * 0.005
        return weighted_choice(
            self.rng,
            [
                ("inside_zone", max(0.10, inside)),
                ("outside_zone", max(0.10, outside)),
                ("power", max(0.10, power)),
                ("draw", max(0.10, draw)),
            ],
        )

    def choose_pass_concept(self, offense: TeamSnapshot, defense: TeamSnapshot, down: int, distance: int, field_pos: int) -> str:
        receiver_profiles = [
            receiver_behavior_profile(player)
            for player in offense.receiving_options()
            if player.position in {"WR", "TE"}
        ]
        vertical = average([profile.vertical_intent for profile in receiver_profiles])
        route = average([profile.route_pacing for profile in receiver_profiles])
        middle = average([profile.middle_comfort for profile in receiver_profiles])
        yac = average([profile.yac_intent for profile in receiver_profiles])
        contested = average([profile.contested_alpha for profile in receiver_profiles])
        return weighted_choice(
            self.rng,
            [
                ("quick", (1.25 if distance <= 5 else 0.85) + (route - 50) * 0.0035),
                ("short", 1.15 + (middle - 50) * 0.0030 + (route - 50) * 0.0025),
                ("intermediate", 1.00 + (middle - 50) * 0.0025 + (contested - 50) * 0.0018),
                ("deep", (0.45 if down < 3 else 0.70) + (vertical - 50) * 0.0045 + (contested - 50) * 0.0018),
                ("screen", 0.45 + (yac - 50) * 0.0035),
            ],
        )

    def rb_carry_weight(
        self,
        player: PlayerSnapshot,
        idx: int,
        *,
        down: int,
        distance: int,
        field_pos: int,
    ) -> float:
        profile = rb_behavior_profile(player)
        talent = weighted_average(player, RB_RUN_WEIGHTS)
        overall = float(player.metadata.get("overall") or player.general_score())
        weight = 1.0 / (idx + 1.65)
        weight *= 1.0 + clamp((talent - 70) * 0.015, -0.30, 0.36)
        weight *= 1.0 + clamp((overall - 72) * 0.018, -0.34, 0.26)
        if idx >= 1 and overall < 72:
            weight *= 0.84
        if idx >= 2:
            weight *= 0.72
        weight *= 1.0 + (profile.early_down_gravity - 50) * 0.010
        if distance <= 3 or field_pos >= 85:
            weight *= 1.0 + (profile.short_yardage_trust - 50) * 0.014
        if down >= 3 and distance >= 6:
            weight *= 1.0 + (profile.pass_game_usage - 50) * 0.006
        return max(0.05, weight)

    def state_snapshot(self) -> tuple[dict[int, int], dict[int, Counter], dict[int, Counter], dict[int, int]]:
        return (
            dict(self.score),
            {team_id: Counter(stats) for team_id, stats in self.team_stats.items()},
            {player_id: Counter(stats) for player_id, stats in self.player_stats.items()},
            dict(self.timeouts),
        )

    def restore_state_snapshot(self, snapshot: tuple[dict[int, int], dict[int, Counter], dict[int, Counter], dict[int, int]]) -> None:
        score, team_stats, player_stats, timeouts = snapshot
        self.score = dict(score)
        self.team_stats = defaultdict(Counter, {team_id: Counter(stats) for team_id, stats in team_stats.items()})
        self.player_stats = defaultdict(Counter, {player_id: Counter(stats) for player_id, stats in player_stats.items()})
        self.timeouts = dict(timeouts)

    def penalty_team(self, flag: PenaltyFlag, offense: TeamSnapshot, defense: TeamSnapshot) -> TeamSnapshot:
        return offense if flag.side == "offense" else defense

    def mark_penalty_accepted(self, flag: PenaltyFlag, offense: TeamSnapshot, defense: TeamSnapshot, enforced_yards: int, first_down: bool) -> None:
        penalized = self.penalty_team(flag, offense, defense)
        self.team_stats[penalized.team_id]["penalties"] += 1
        self.team_stats[penalized.team_id]["penalty_yards"] += abs(enforced_yards)
        if first_down and flag.side == "defense":
            self.team_stats[offense.team_id]["first_downs"] += 1
            self.team_stats[offense.team_id]["penalty_first_downs"] += 1

    def mark_penalty_declined(self, flag: PenaltyFlag, offense: TeamSnapshot, defense: TeamSnapshot) -> None:
        penalized = self.penalty_team(flag, offense, defense)
        self.team_stats[penalized.team_id]["declined_penalties"] += 1

    def mark_offsetting_penalties(self, flags: list[PenaltyFlag], offense: TeamSnapshot, defense: TeamSnapshot) -> None:
        for flag in flags:
            penalized = self.penalty_team(flag, offense, defense)
            self.team_stats[penalized.team_id]["offsetting_penalties"] += 1

    def apply_penalty_distance(self, spot: int, signed_yards: int) -> tuple[int, int]:
        spot = int(clamp(spot, 1, 99))
        if signed_yards > 0:
            max_yards = max(1, math.ceil((100 - spot) / 2)) if signed_yards > (100 - spot) / 2 else signed_yards
            new_field = int(clamp(spot + min(signed_yards, max_yards), 1, 99))
        else:
            distance = abs(signed_yards)
            max_yards = max(1, math.ceil(spot / 2)) if distance > spot / 2 else distance
            new_field = int(clamp(spot - min(distance, max_yards), 1, 99))
        return new_field, new_field - spot

    def penalty_decision(
        self,
        flag: PenaltyFlag,
        *,
        snap_down: int,
        snap_distance: int,
        snap_field: int,
        dead_field: int,
        play_outcome: str | None = None,
    ) -> PenaltyDecision:
        line_to_gain = min(100, snap_field + snap_distance)
        if flag.side == "offense":
            spot = dead_field if flag.enforcement == "dead_ball" else snap_field
            new_field, enforced_yards = self.apply_penalty_distance(spot, -flag.yards)
            new_down = snap_down + 1 if flag.loss_of_down else snap_down
            if new_down > 4:
                return PenaltyDecision(
                    field_pos=new_field,
                    down=new_down,
                    distance=max(1, line_to_gain - new_field),
                    enforced_yards=enforced_yards,
                    turnover_on_downs=True,
                )
            return PenaltyDecision(
                field_pos=new_field,
                down=new_down,
                distance=max(1, line_to_gain - new_field),
                enforced_yards=enforced_yards,
            )

        if flag.enforcement == "spot" and flag.spot_yards is not None:
            new_field = int(clamp(snap_field + flag.spot_yards, 1, 99))
            enforced_yards = new_field - snap_field
        elif flag.enforcement == "best_previous_or_dead":
            previous_field, previous_yards = self.apply_penalty_distance(snap_field, flag.yards)
            dead_ball_field, dead_ball_yards = self.apply_penalty_distance(dead_field, flag.yards)
            if play_outcome in {"turnover", "defensive_touchdown"}:
                new_field, enforced_yards = previous_field, previous_yards
            elif dead_ball_field >= previous_field:
                new_field, enforced_yards = dead_ball_field, dead_ball_yards
            else:
                new_field, enforced_yards = previous_field, previous_yards
        elif flag.enforcement == "dead_ball":
            new_field, enforced_yards = self.apply_penalty_distance(dead_field, flag.yards)
        else:
            new_field, enforced_yards = self.apply_penalty_distance(snap_field, flag.yards)

        first_down = flag.automatic_first_down or new_field >= line_to_gain
        if first_down:
            return PenaltyDecision(
                field_pos=new_field,
                down=1,
                distance=min(10, max(1, 100 - new_field)),
                enforced_yards=enforced_yards,
                first_down=True,
                keeps_play=flag.enforcement in {"dead_ball", "best_previous_or_dead"} and play_outcome not in {"turnover", "defensive_touchdown"},
            )
        return PenaltyDecision(
            field_pos=new_field,
            down=snap_down,
            distance=max(1, line_to_gain - new_field),
            enforced_yards=enforced_yards,
            keeps_play=flag.enforcement in {"dead_ball", "best_previous_or_dead"} and play_outcome not in {"turnover", "defensive_touchdown"},
        )

    def penalty_description(self, flag: PenaltyFlag, team: TeamSnapshot, decision: PenaltyDecision, accepted: bool = True) -> str:
        yards = abs(decision.enforced_yards) if accepted else flag.yards
        if accepted:
            suffix = ", automatic first down" if decision.first_down and flag.side == "defense" else ""
            if flag.loss_of_down:
                suffix += ", loss of down"
            return f"Penalty: {flag.label} on {team.abbreviation}, {yards} yards accepted{suffix}."
        return f"Penalty declined: {flag.label} on {team.abbreviation}."

    def should_accept_penalty(
        self,
        flag: PenaltyFlag,
        decision: PenaltyDecision,
        *,
        snap_down: int,
        snap_distance: int,
        play_yards: int,
        outcome: str,
    ) -> bool:
        if flag.side == "defense":
            if outcome == "touchdown":
                return False
            if outcome in {"turnover", "defensive_touchdown"}:
                return True
            if decision.first_down and play_yards < snap_distance:
                return True
            if decision.enforced_yards >= max(1, play_yards):
                return True
            return False

        if outcome in {"turnover", "defensive_touchdown"}:
            return False
        if outcome == "touchdown":
            return True
        if flag.loss_of_down:
            return play_yards >= 0 or snap_down < 4
        if snap_down >= 3 and play_yards < snap_distance:
            return False
        if play_yards < 0:
            return False
        return True

    def make_presnap_penalty(self, offense: TeamSnapshot, defense: TeamSnapshot, play_type: str) -> PenaltyFlag | None:
        offense_discipline = offense.discipline_score()
        defense_discipline = defense.discipline_score()
        chance = 0.017 + max(0, 62 - offense_discipline) * 0.00020 + max(0, 62 - defense_discipline) * 0.00012
        if self.rng.random() >= chance:
            return None
        if self.rng.random() < 0.62:
            label = "false start" if self.rng.random() < 0.70 else "delay of game"
            return PenaltyFlag(label=label, side="offense", yards=5, timing="dead_ball", no_play=True)
        return PenaltyFlag(label="neutral zone infraction", side="defense", yards=5, timing="dead_ball", no_play=True)

    def offensive_live_penalty(self, play_type: str) -> PenaltyFlag:
        if play_type == "pass":
            label = weighted_choice(
                self.rng,
                [
                    ("offensive holding", 0.42),
                    ("offensive pass interference", 0.18),
                    ("intentional grounding", 0.16),
                    ("illegal formation", 0.14),
                    ("ineligible player downfield", 0.10),
                ],
            )
        else:
            label = weighted_choice(
                self.rng,
                [
                    ("offensive holding", 0.62),
                    ("illegal formation", 0.20),
                    ("illegal block above the waist", 0.18),
                ],
            )
        if label == "intentional grounding":
            return PenaltyFlag(label=label, side="offense", yards=10, timing="live_ball", loss_of_down=True)
        yards = 5 if label in {"illegal formation", "ineligible player downfield"} else 10
        return PenaltyFlag(label=label, side="offense", yards=yards, timing="live_ball")

    def defensive_live_penalty(self, play_type: str, field_pos: int) -> PenaltyFlag:
        if play_type == "pass":
            label = weighted_choice(
                self.rng,
                [
                    ("defensive offside", 0.18),
                    ("defensive holding", 0.22),
                    ("illegal contact", 0.16),
                    ("defensive pass interference", 0.20),
                    ("roughing the passer", 0.12),
                    ("facemask", 0.07),
                    ("unnecessary roughness", 0.05),
                ],
            )
        else:
            label = weighted_choice(
                self.rng,
                [
                    ("defensive offside", 0.38),
                    ("defensive holding", 0.18),
                    ("facemask", 0.24),
                    ("unnecessary roughness", 0.20),
                ],
            )
        if label == "defensive pass interference":
            max_spot = max(1, 99 - field_pos)
            spot_yards = int(clamp(round(self.rng.gauss(17, 9)), 1, max_spot))
            return PenaltyFlag(label=label, side="defense", yards=spot_yards, timing="live_ball", enforcement="spot", automatic_first_down=True, spot_yards=spot_yards)
        if label in {"roughing the passer", "facemask", "unnecessary roughness"}:
            return PenaltyFlag(label=label, side="defense", yards=15, timing="live_ball", enforcement="best_previous_or_dead", automatic_first_down=True, personal_foul=True)
        if label in {"defensive holding", "illegal contact"}:
            return PenaltyFlag(label=label, side="defense", yards=5, timing="live_ball", automatic_first_down=True)
        return PenaltyFlag(label=label, side="defense", yards=5, timing="live_ball")

    def maybe_live_penalties(self, offense: TeamSnapshot, defense: TeamSnapshot, play_type: str, field_pos: int) -> list[PenaltyFlag]:
        offense_discipline = offense.discipline_score()
        defense_discipline = defense.discipline_score()
        base = 0.041
        penalty_chance = base + max(0, 60 - offense_discipline) * 0.00045 + max(0, 60 - defense_discipline) * 0.00025
        if self.rng.random() >= penalty_chance:
            return []
        if self.rng.random() < 0.54:
            flags = [self.offensive_live_penalty(play_type)]
        else:
            flags = [self.defensive_live_penalty(play_type, field_pos)]
        if self.rng.random() < 0.055:
            flags.append(self.defensive_live_penalty(play_type, field_pos) if flags[0].side == "offense" else self.offensive_live_penalty(play_type))
        return flags

    def run_play(
        self,
        offense: TeamSnapshot,
        defense: TeamSnapshot,
        down: int,
        distance: int,
        field_pos: int,
    ) -> tuple[str, int, int, int, str, TeamSnapshot, int, int, int, PlayerSnapshot | None, PlayerSnapshot | None]:
        qb = self.active_starter(offense, "QB")
        rb_candidates = self.eligible_slot_candidates(offense, "RB")[:3]
        if not rb_candidates:
            rb_candidates = [self.active_starter(offense, "RB")]
        if len(rb_candidates) > 2:
            starter_score = offense.score_for_slot(rb_candidates[0], "RB")
            kept = rb_candidates[:2]
            for extra in rb_candidates[2:]:
                extra_score = offense.score_for_slot(extra, "RB")
                extra_overall = float(extra.metadata.get("overall") or extra.general_score())
                extra_potential = float(extra.metadata.get("potential") or extra_overall)
                is_rookie = bool(int(extra.metadata.get("is_rookie") or 0))
                if extra_score >= starter_score - 4.0:
                    kept.append(extra)
                elif is_rookie and extra_overall >= 70 and extra_potential >= 78 and extra_score >= starter_score - 8.0:
                    kept.append(extra)
            rb_candidates = kept
        qb_scramble_score = weighted_average(qb, QB_SCRAMBLE_WEIGHTS)
        profile = qb_behavior_profile(qb)
        qb_run_chance = clamp(
            (qb_scramble_score - 70) * 0.0030
            + (profile.scramble_trigger - 60) * 0.0010
            + (profile.pocket_drift - 60) * 0.0004,
            0.0,
            0.105,
        )
        if distance <= 3 and field_pos >= 50:
            qb_run_chance += 0.018
        if field_pos >= 80 and distance <= 5:
            qb_run_chance += 0.014
        if profile.pocket_drift >= 85:
            qb_run_chance += (profile.pocket_drift - 85) * 0.0015
        if down >= 3 and distance >= 7:
            qb_run_chance *= 0.55
        designed_qb_run = qb_scramble_score >= 68 and self.rng.random() < clamp(qb_run_chance, 0.0, 0.13)
        runner = qb if designed_qb_run else weighted_choice(
            self.rng,
            [
                (
                    player,
                    self.rb_carry_weight(
                        player,
                        idx,
                        down=down,
                        distance=distance,
                        field_pos=field_pos,
                    ),
                )
                for idx, player in enumerate(rb_candidates)
            ],
        )
        concept = self.choose_run_concept(offense, defense, down, distance, field_pos, runner)
        self._last_play_concept = concept
        self.set_snap_override(
            offense,
            "offensive_snaps",
            self.offensive_snap_players(offense, concept, "run", ball_carrier=runner),
        )
        defense_snap_players = self.defensive_snap_players(defense, "run", concept)
        self.set_snap_override(defense, "defensive_snaps", defense_snap_players)
        run_block = offense.run_block_score()
        run_def = defense.run_defense_score()
        is_qb_run = runner.player_id == qb.player_id
        rb_profile = None if is_qb_run else rb_behavior_profile(runner)
        runner_score = weighted_average(runner, RB_RUN_WEIGHTS if not is_qb_run else QB_SCRAMBLE_WEIGHTS)
        runner_overall = float(runner.metadata.get("overall") or runner.general_score())
        tackling = defense.tackling_score()
        trench_advantage = run_block - run_def
        runner_advantage = runner_score - tackling
        replacement_penalty = max(0.0, 72.0 - runner_overall)
        low_run_trait_penalty = max(0.0, 70.0 - runner_score)

        stuff_chance = clamp(0.160 - trench_advantage * 0.0020 - runner_advantage * 0.00100, 0.060, 0.290)
        explosive_chance = clamp(0.024 + (runner.rating("speed") - 70) * 0.00135 + (runner.rating("elusiveness") - tackling) * 0.00105, 0.008, 0.095)
        if is_qb_run:
            stuff_chance = clamp(stuff_chance - 0.025 - (profile.pressure_escape - 70) * 0.00035, 0.035, 0.235)
            explosive_chance = clamp(explosive_chance + 0.020 + (profile.broken_play_creation - 70) * 0.0008, 0.025, 0.145)
        elif rb_profile:
            stuff_chance += replacement_penalty * 0.0026 + low_run_trait_penalty * 0.0015
            bounce_risk = max(0.0, rb_profile.bounce_tendency - rb_profile.one_cut_decisiveness)
            stuff_chance += bounce_risk * 0.00085
            stuff_chance -= (rb_profile.one_cut_decisiveness - 50) * 0.00062
            explosive_chance += (rb_profile.home_run_hunting - 50) * 0.00050
            explosive_chance += (rb_profile.bounce_tendency - 50) * 0.00018
            explosive_chance -= replacement_penalty * 0.0012 + low_run_trait_penalty * 0.0008
            if concept == "outside_zone":
                explosive_chance += (rb_profile.bounce_tendency - 50) * 0.00028
                stuff_chance += max(0.0, rb_profile.bounce_tendency - 76) * 0.00045
            if concept == "power":
                stuff_chance -= (rb_profile.contact_appetite - 50) * 0.00048
            stuff_chance = clamp(stuff_chance, 0.045, 0.305)
            explosive_cap = 0.115
            if runner_overall < 65:
                explosive_cap = 0.040
            elif runner_overall < 70:
                explosive_cap = 0.058
            elif runner_overall < 75:
                explosive_cap = 0.078
            explosive_chance = clamp(explosive_chance, 0.004, explosive_cap)

        if self.rng.random() < stuff_chance:
            yards = int(round(self.rng.gauss(-1.2, 1.5)))
        else:
            mean = 3.42 + trench_advantage * 0.036 + runner_advantage * 0.023
            if is_qb_run:
                mean += 1.05 + (profile.pressure_escape - 70) * 0.025
            elif rb_profile:
                mean -= replacement_penalty * 0.040 + low_run_trait_penalty * 0.020
                mean += (rb_profile.patience - 50) * 0.010
                mean += (rb_profile.one_cut_decisiveness - 50) * 0.012
                if concept == "power" or distance <= 2:
                    mean += (rb_profile.contact_appetite - 50) * 0.017
                if concept == "outside_zone":
                    mean += (rb_profile.bounce_tendency - 50) * 0.007
                if concept == "draw":
                    mean += (rb_profile.space_creation - 50) * 0.008
            yards = int(round(self.rng.gauss(mean, 3.0)))
            if self.rng.random() < explosive_chance:
                yards += int(round(self.rng.lognormvariate(1.88, 0.38)))
        yards = int(clamp(yards, -8, 80))
        if field_pos + yards >= 100:
            yards = max(0, 100 - field_pos)

        new_field = field_pos + yards
        touchdown = new_field >= 100
        tackler = None if touchdown else self.select_run_tackler(defense, yards, defense_snap_players)

        fumble_kind = "scramble" if is_qb_run else "run"
        if tackler and self.rng.random() < self.fumble_event_chance(runner, tackler, defense, yards, play_kind=fumble_kind):
            recovery_spot = int(clamp(new_field, 1, 99))
            defense_recovered = self.rng.random() < self.defense_fumble_recovery_chance(
                runner,
                tackler,
                yards,
                play_kind=fumble_kind,
            )
            return_yards = max(0, int(round(self.rng.gauss(7, 6)))) if defense_recovered else 0
            return_field = 100 - recovery_spot + return_yards
            turnover_field = int(clamp(return_field, 1, 99))
            self.team_stats[offense.team_id]["plays"] += 1
            self.team_stats[offense.team_id]["rush_attempts"] += 1
            self.team_stats[offense.team_id]["rush_yards"] += yards
            self.team_stats[offense.team_id]["total_yards"] += yards
            self.team_stats[offense.team_id]["fumbles"] += 1
            self.player_stats[runner.player_id]["rush_attempts"] += 1
            self.player_stats[runner.player_id]["rush_yards"] += yards
            self.player_stats[runner.player_id]["fumbles"] += 1
            self.credit_tackle(defense, tackler, yards, play_kind=fumble_kind)
            self.player_stats[tackler.player_id]["forced_fumbles"] += 1
            if not defense_recovered:
                self.player_stats[runner.player_id]["offensive_fumble_recoveries"] += 1
                desc = f"{runner.name} runs for {yards} and fumbles, but {offense.abbreviation} recovers."
                return "normal", yards, 0, 0, desc, offense, int(clamp(new_field, 1, 99)), down, distance, runner, tackler
            self.team_stats[offense.team_id]["turnovers"] += 1
            self.team_stats[offense.team_id]["fumbles_lost"] += 1
            self.player_stats[runner.player_id]["fumbles_lost"] += 1
            self.player_stats[tackler.player_id]["fumble_recoveries"] += 1
            self.player_stats[tackler.player_id]["fumble_return_yards"] += return_yards
            if return_field >= 100:
                self.add_score(defense, 6)
                self.team_stats[defense.team_id]["defensive_tds"] += 1
                self.player_stats[tackler.player_id]["defensive_tds"] += 1
                self.player_stats[tackler.player_id]["fumble_return_tds"] += 1
                try_result = self.try_after_touchdown(defense, offense)
                desc = f"{runner.name} runs for {yards} but fumbles. {tackler.name} returns it for a touchdown. {try_result}"
                return "defensive_touchdown", yards, 0, 0, desc, offense, 25, 1, 10, runner, tackler
            desc = f"{runner.name} runs for {yards} but fumbles. {tackler.name} recovers."
            return "turnover", yards, 0, 0, desc, defense, turnover_field, 1, 10, runner, tackler

        self.team_stats[offense.team_id]["plays"] += 1
        self.team_stats[offense.team_id]["rush_attempts"] += 1
        self.team_stats[offense.team_id]["rush_yards"] += yards
        self.team_stats[offense.team_id]["total_yards"] += yards
        self.player_stats[runner.player_id]["rush_attempts"] += 1
        self.player_stats[runner.player_id]["rush_yards"] += yards
        if tackler:
            self.credit_tackle(defense, tackler, yards, play_kind="run")
        if touchdown:
            self.add_score(offense, 6)
            self.player_stats[runner.player_id]["rush_tds"] += 1
            try_result = self.try_after_touchdown(offense, defense)
            desc = f"{runner.name} scores on a {max(0, 100 - field_pos)} yard run. {try_result}"
            return "touchdown", yards, 6, 0, desc, self.opponent(offense), 25, 1, 10, runner, None

        desc = f"{runner.name} runs {yards} yards"
        if tackler:
            desc += f", tackled by {tackler.name}"
        return "normal", yards, 0, 0, desc + ".", offense, int(clamp(new_field, 1, 99)), down, distance, runner, tackler

    def qb_scramble_play(
        self,
        offense: TeamSnapshot,
        defense: TeamSnapshot,
        qb: PlayerSnapshot,
        field_pos: int,
        *,
        pressured: bool,
        defense_snap_players: list[PlayerSnapshot] | None = None,
    ) -> tuple[str, int, int, int, str, TeamSnapshot, int, int, int, PlayerSnapshot | None, PlayerSnapshot | None]:
        qb_scramble_score = weighted_average(qb, QB_SCRAMBLE_WEIGHTS)
        profile = qb_behavior_profile(qb)
        run_def = defense.run_defense_score()
        mean = (
            5.7
            + (qb_scramble_score - 70) * 0.100
            + (profile.pressure_escape - 70) * 0.035
            + (profile.broken_play_creation - 70) * 0.015
            + (profile.pocket_drift - 70) * 0.035
            - (run_def - 65) * 0.020
        )
        if pressured:
            mean -= 0.9
        yards = int(round(self.rng.gauss(mean, 4.0)))
        explosive_chance = clamp(
            0.028
            + (qb.rating("speed") - 70) * 0.0016
            + (qb.rating("elusiveness") - defense.tackling_score()) * 0.0010,
            0.006,
            0.130,
        )
        explosive_chance = clamp(explosive_chance + (profile.broken_play_creation - 70) * 0.0008, 0.006, 0.150)
        if self.rng.random() < explosive_chance:
            yards += int(round(self.rng.lognormvariate(1.95, 0.42)))
        yards = int(clamp(yards, -5, 45))
        if field_pos + yards >= 100:
            yards = max(0, 100 - field_pos)

        new_field = field_pos + yards
        touchdown = new_field >= 100
        tackler = None if touchdown else self.select_run_tackler(defense, yards, defense_snap_players)
        self.team_stats[offense.team_id]["plays"] += 1
        self.team_stats[offense.team_id]["rush_attempts"] += 1
        self.team_stats[offense.team_id]["rush_yards"] += yards
        self.team_stats[offense.team_id]["total_yards"] += yards
        self.player_stats[qb.player_id]["rush_attempts"] += 1
        self.player_stats[qb.player_id]["rush_yards"] += yards
        if tackler:
            self.credit_tackle(defense, tackler, yards, play_kind="scramble")
        if touchdown:
            self.add_score(offense, 6)
            self.player_stats[qb.player_id]["rush_tds"] += 1
            try_result = self.try_after_touchdown(offense, defense)
            desc = f"{qb.name} scrambles for a {max(0, 100 - field_pos)} yard touchdown. {try_result}"
            return "touchdown", yards, 6, 0, desc, self.opponent(offense), 25, 1, 10, None, None

        desc = f"{qb.name} scrambles for {yards} yards"
        if tackler:
            desc += f", tackled by {tackler.name}"
        return "normal", yards, 0, 0, desc + ".", offense, int(clamp(new_field, 1, 99)), 1, 10, None, tackler

    def pass_play(self, offense: TeamSnapshot, defense: TeamSnapshot, down: int, distance: int, field_pos: int) -> tuple[str, int, int, int, str, TeamSnapshot, int, int, int, PlayerSnapshot | None, PlayerSnapshot | None]:
        qb = self.active_starter(offense, "QB")
        profile = qb_behavior_profile(qb)
        concept = self.choose_pass_concept(offense, defense, down, distance, field_pos)
        self._last_play_concept = concept
        depth_profile = {
            "quick": (3, 2),
            "short": (6, 3),
            "intermediate": (11, 4),
            "deep": (21, 7),
            "screen": (0, 2),
        }[concept]
        offense_snap_players = self.offensive_snap_players(offense, concept, "pass")
        self.set_snap_override(offense, "offensive_snaps", offense_snap_players)
        defense_snap_players = self.defensive_snap_players(defense, "pass", concept)
        self.set_snap_override(defense, "defensive_snaps", defense_snap_players)
        snap_rushers = self.pass_rushers_from_snap(defense, defense_snap_players)
        pass_block = offense.pass_block_score()
        pass_rush = defense.pass_rush_score(snap_rushers)
        qb_processing = average([qb.rating("processing_speed"), qb.rating("play_recognition"), qb.rating("throw_release")])
        pressure_chance = clamp(0.255 + (pass_rush - pass_block) * 0.0055 - (qb_processing - 65) * 0.0013, 0.070, 0.530)
        pressured = self.rng.random() < pressure_chance
        concept_sack_modifier = {
            "screen": -0.095,
            "quick": -0.075,
            "short": -0.035,
            "intermediate": 0.020,
            "deep": 0.065,
        }[concept]
        escape_score = average([qb.rating("speed"), qb.rating("acceleration"), qb.rating("agility"), qb.rating("processing_speed")])
        sack_chance = clamp(
            0.242
            + concept_sack_modifier
            + (pass_rush - pass_block) * 0.0041
            - (qb.rating("throw_release") - 65) * 0.0020
            - (escape_score - 65) * 0.0011,
            0.044,
            0.360,
        )
        if pressured and self.rng.random() < sack_chance:
            rusher = self.select_pass_rusher(defense, snap_rushers)
            loss = int(clamp(round(self.rng.gauss(6.3 + (pass_rush - pass_block) * 0.035, 2.2)), 1, 15))
            self.team_stats[offense.team_id]["plays"] += 1
            self.team_stats[offense.team_id]["sacks_allowed"] += 1
            self.team_stats[offense.team_id]["pass_yards"] -= loss
            self.team_stats[offense.team_id]["total_yards"] -= loss
            self.team_stats[defense.team_id]["sacks"] += 1
            self.player_stats[qb.player_id]["sacks_taken"] += 1
            self.player_stats[rusher.player_id]["sacks"] += 1
            self.credit_tackle(defense, rusher, -loss, play_kind="sack", force_solo=True)
            if field_pos - loss > 0 and self.rng.random() < self.fumble_event_chance(qb, rusher, defense, -loss, play_kind="sack"):
                recovery_spot = int(clamp(field_pos - loss, 1, 99))
                defense_recovered = self.rng.random() < self.defense_fumble_recovery_chance(
                    qb,
                    rusher,
                    -loss,
                    play_kind="sack",
                )
                return_yards = max(0, int(round(self.rng.gauss(5, 6)))) if defense_recovered else 0
                return_field = 100 - recovery_spot + return_yards
                turnover_field = int(clamp(return_field, 1, 99))
                self.team_stats[offense.team_id]["fumbles"] += 1
                self.player_stats[qb.player_id]["fumbles"] += 1
                self.player_stats[rusher.player_id]["forced_fumbles"] += 1
                if not defense_recovered:
                    self.player_stats[qb.player_id]["offensive_fumble_recoveries"] += 1
                    desc = f"{rusher.name} sacks {qb.name} for a loss of {loss}. {qb.name} fumbles, but {offense.abbreviation} recovers."
                    return "normal", -loss, 0, 0, desc, offense, int(clamp(field_pos - loss, 1, 99)), down, distance, qb, rusher
                self.team_stats[offense.team_id]["turnovers"] += 1
                self.team_stats[offense.team_id]["fumbles_lost"] += 1
                self.player_stats[qb.player_id]["fumbles_lost"] += 1
                self.player_stats[rusher.player_id]["fumble_recoveries"] += 1
                self.player_stats[rusher.player_id]["fumble_return_yards"] += return_yards
                if return_field >= 100:
                    self.add_score(defense, 6)
                    self.team_stats[defense.team_id]["defensive_tds"] += 1
                    self.player_stats[rusher.player_id]["defensive_tds"] += 1
                    self.player_stats[rusher.player_id]["fumble_return_tds"] += 1
                    try_result = self.try_after_touchdown(defense, offense)
                    desc = f"{rusher.name} strip-sacks {qb.name} and returns it for a touchdown. {try_result}"
                    return "defensive_touchdown", -loss, 0, 0, desc, offense, 25, 1, 10, qb, rusher
                desc = f"{rusher.name} strip-sacks {qb.name}. {rusher.name} recovers."
                return "turnover", -loss, 0, 0, desc, defense, turnover_field, 1, 10, qb, rusher
            desc = f"{rusher.name} sacks {qb.name} for a loss of {loss}."
            return "normal", -loss, 0, 0, desc, offense, int(clamp(field_pos - loss, 1, 99)), down, distance, qb, rusher

        qb_scramble_score = weighted_average(qb, QB_SCRAMBLE_WEIGHTS)
        if pressured:
            scramble_chance = clamp(
                (qb_scramble_score - 66) * 0.0060
                + (profile.pressure_escape - 60) * 0.0030
                + (profile.scramble_trigger - 60) * 0.0030
                + (profile.pocket_drift - 65) * 0.0015
                - (qb.rating("throw_release") - 72) * 0.0015,
                0.0,
                0.32,
            )
            if profile.pocket_drift >= 85:
                scramble_chance += clamp(
                    (profile.pocket_drift - 85) * 0.0040
                    + (profile.broken_play_creation - 80) * 0.0010,
                    0.0,
                    0.070,
                )
        elif concept in {"intermediate", "deep"}:
            scramble_chance = clamp(
                (profile.pocket_drift - 72) * 0.0015
                + (profile.scramble_trigger - 72) * 0.0012,
                0.0,
                0.08,
            )
        else:
            scramble_chance = 0.0
        if self.rng.random() < scramble_chance:
            self._last_play_concept = "scramble"
            return self.qb_scramble_play(
                offense,
                defense,
                qb,
                field_pos,
                pressured=pressured,
                defense_snap_players=defense_snap_players,
            )

        target = self.select_receiver(offense, concept, offense_snap_players)
        defender = self.select_coverage_defender(defense, target, concept, defense_snap_players)
        receiver_profile = receiver_behavior_profile(target) if target.position in {"WR", "TE"} else None
        air_yards = int(clamp(round(self.rng.gauss(*depth_profile)), -2, 48))
        if receiver_profile and concept == "deep":
            air_yards += int(round((receiver_profile.vertical_intent - 50) * 0.035))
        elif receiver_profile and concept in {"quick", "short"}:
            air_yards += int(round((receiver_profile.middle_comfort - 50) * 0.012))
        if field_pos + air_yards > 99:
            air_yards = max(1, 100 - field_pos)

        qb_accuracy_key = "pass_accuracy_short"
        if air_yards >= 18:
            qb_accuracy_key = "pass_accuracy_deep"
        elif air_yards >= 9:
            qb_accuracy_key = "pass_accuracy_mid"

        qb_score = average([
            qb.rating(qb_accuracy_key),
            qb.rating("platform_control"),
            qb.rating("composure"),
            qb.rating("processing_speed"),
        ])
        receiver_score = weighted_average(target, RECEIVER_WEIGHTS)
        defender_profile = secondary_behavior_profile(defender) if defender.position in SECONDARY_POSITIONS else None
        coverage_score = weighted_average(defender, COVERAGE_WEIGHTS)
        if defender_profile:
            if concept == "quick":
                coverage_score += (defender_profile.break_trigger - 50) * 0.018
                coverage_score += (defender_profile.slot_traffic - 50) * 0.010
            elif concept in {"short", "intermediate"}:
                coverage_score += (defender_profile.man_mirror - 50) * 0.014
                coverage_score += (defender_profile.zone_eye_discipline - 50) * 0.014
                coverage_score += (defender_profile.break_trigger - 50) * 0.014
            elif concept == "deep":
                coverage_score += (defender_profile.deep_range - 50) * 0.020
                coverage_score += (defender_profile.ball_play_timing - 50) * 0.014
                coverage_score += (defender_profile.catch_point_compete - 50) * 0.010
            elif concept == "screen":
                coverage_score += (defender_profile.run_support_fit - 50) * 0.014
                coverage_score += (defender_profile.tackle_finish - 50) * 0.008
        separation = receiver_score - coverage_score
        depth_penalty = max(0, air_yards - 5) * 0.014
        completion_chance = 0.585 + (qb_score - 65) * 0.0036 + separation * 0.0042 - depth_penalty
        if receiver_profile:
            completion_chance += (receiver_profile.route_pacing - 50) * 0.00075
            completion_chance += (receiver_profile.catch_security - 50) * 0.00055
            if concept == "quick":
                completion_chance += (receiver_profile.release_urgency - 50) * 0.00080
            elif concept in {"short", "intermediate"}:
                completion_chance += (receiver_profile.middle_comfort - 50) * 0.00070
            elif concept == "deep":
                completion_chance += (receiver_profile.sideline_awareness - 50) * 0.00070
                completion_chance += (receiver_profile.contested_alpha - 50) * 0.00045
        if concept == "screen":
            completion_chance += 0.07
        if pressured:
            completion_chance -= 0.120
            if receiver_profile:
                completion_chance += (receiver_profile.scramble_drill - 50) * 0.00070
        completion_chance = clamp(completion_chance, 0.205, 0.865)

        interception_chance = 0.0108 + max(0, air_yards - 6) * 0.00072
        interception_chance += max(0, coverage_score - qb_score) * 0.00038
        interception_chance += max(0, -separation) * 0.00018
        interception_chance -= max(0, separation) * 0.00006
        interception_chance += max(0, 64 - qb.rating("discipline")) * 0.00018
        interception_chance += (profile.deep_aggression - 50) * 0.00008
        interception_chance += (profile.sack_risk - 50) * 0.000045
        interception_chance -= (profile.throwaway_discipline - 50) * 0.00006
        if receiver_profile:
            interception_chance += max(0.0, receiver_profile.vertical_intent - 78) * 0.00008
            interception_chance += max(0.0, receiver_profile.target_gravity - 84) * 0.00005
            interception_chance -= (receiver_profile.route_pacing - 50) * 0.00007
            interception_chance -= (receiver_profile.catch_security - 50) * 0.00006
        if defender_profile:
            interception_chance += (defender_profile.ball_play_timing - 50) * 0.00010
            interception_chance += (defender_profile.break_trigger - 50) * 0.00006
            if concept == "deep":
                interception_chance += (defender_profile.deep_range - 50) * 0.00006
                interception_chance += (defender_profile.catch_point_compete - 50) * 0.00004
            if defender_profile.press_timing >= 86 and air_yards <= 6:
                interception_chance += 0.0015
        if concept == "screen":
            interception_chance -= 0.0050
        elif concept == "quick":
            interception_chance -= 0.0025
        if pressured:
            interception_chance += 0.0048
        interception_chance = clamp(interception_chance, 0.0025, 0.052)

        self.team_stats[offense.team_id]["plays"] += 1
        self.team_stats[offense.team_id]["pass_attempts"] += 1
        self.player_stats[qb.player_id]["pass_attempts"] += 1
        self.player_stats[target.player_id]["targets"] += 1

        if self.rng.random() < interception_chance:
            return_yards = max(0, int(round(self.rng.gauss(11, 9))))
            pick_spot = int(clamp(field_pos + max(0, air_yards), 1, 99))
            return_field = 100 - pick_spot + return_yards
            new_field = int(clamp(return_field, 1, 99))
            self.team_stats[offense.team_id]["turnovers"] += 1
            self.team_stats[offense.team_id]["interceptions_thrown"] += 1
            self.team_stats[defense.team_id]["interceptions"] += 1
            self.player_stats[qb.player_id]["interceptions_thrown"] += 1
            self.player_stats[defender.player_id]["interceptions"] += 1
            self.player_stats[defender.player_id]["interception_return_yards"] += return_yards
            self.player_stats[defender.player_id]["pass_deflections"] += 1
            if return_field >= 100:
                self.add_score(defense, 6)
                self.team_stats[defense.team_id]["defensive_tds"] += 1
                self.player_stats[defender.player_id]["defensive_tds"] += 1
                self.player_stats[defender.player_id]["interception_return_tds"] += 1
                try_result = self.try_after_touchdown(defense, offense)
                desc = f"{qb.name} is intercepted by {defender.name}, who returns it for a touchdown. {try_result}"
                return "defensive_touchdown", 0, 0, 0, desc, offense, 25, 1, 10, target, defender
            desc = f"{qb.name} is intercepted by {defender.name} targeting {target.name}."
            return "turnover", 0, 0, 0, desc, defense, new_field, 1, 10, target, defender

        if self.rng.random() > completion_chance:
            pbu_chance = 0.35
            if defender_profile:
                pbu_chance += (defender_profile.break_trigger - 50) * 0.0010
                pbu_chance += (defender_profile.ball_play_timing - 50) * 0.0009
                pbu_chance += max(0.0, -separation) * 0.0008
                pbu_chance -= max(0.0, separation) * 0.00035
                if concept == "deep":
                    pbu_chance += (defender_profile.catch_point_compete - 50) * 0.0007
            if self.rng.random() < clamp(pbu_chance, 0.24, 0.52):
                self.player_stats[defender.player_id]["pass_deflections"] += 1
                desc = f"{qb.name}'s pass for {target.name} is broken up by {defender.name}."
            else:
                desc = f"{qb.name}'s pass for {target.name} falls incomplete."
            return "normal", 0, 0, 0, desc, offense, field_pos, down, distance, target, defender

        yac_score = weighted_average(target, YAC_WEIGHTS)
        tackle_score = defense.tackling_score()
        yac_mean = {
            "quick": 3.3,
            "short": 3.0,
            "intermediate": 2.0,
            "deep": 1.1,
            "screen": 5.8,
        }[concept] + (yac_score - tackle_score) * 0.035 + separation * 0.016
        if receiver_profile:
            yac_mean += (receiver_profile.yac_intent - 50) * 0.018
            if concept == "screen":
                yac_mean += (receiver_profile.yac_intent - 50) * 0.012
            elif concept == "deep":
                yac_mean += (receiver_profile.sideline_awareness - 50) * 0.004
        yac = max(0, int(round(self.rng.gauss(yac_mean, 3.2))))
        yac_break_chance = (yac_score - tackle_score) * 0.0012 + 0.022
        if receiver_profile:
            yac_break_chance += (receiver_profile.yac_intent - 50) * 0.00022
        if self.rng.random() < clamp(yac_break_chance, 0.006, 0.095):
            yac += int(round(self.rng.lognormvariate(1.85, 0.36)))
        yards = int(clamp(max(0, air_yards) + yac, -5, 90))
        if field_pos + yards >= 100:
            yards = max(0, 100 - field_pos)

        new_field = field_pos + yards
        touchdown = new_field >= 100

        if not touchdown and self.rng.random() < self.fumble_event_chance(target, defender, defense, yards, play_kind="pass"):
            fumble_spot = int(clamp(new_field, 1, 99))
            defense_recovered = self.rng.random() < self.defense_fumble_recovery_chance(
                target,
                defender,
                yards,
                play_kind="pass",
            )
            return_yards = max(0, int(round(self.rng.gauss(6, 5)))) if defense_recovered else 0
            return_field = 100 - fumble_spot + return_yards
            turnover_field = int(clamp(return_field, 1, 99))
            self.team_stats[offense.team_id]["pass_completions"] += 1
            self.team_stats[offense.team_id]["pass_yards"] += yards
            self.team_stats[offense.team_id]["total_yards"] += yards
            self.team_stats[offense.team_id]["fumbles"] += 1
            self.player_stats[qb.player_id]["pass_completions"] += 1
            self.player_stats[qb.player_id]["pass_yards"] += yards
            self.player_stats[target.player_id]["receptions"] += 1
            self.player_stats[target.player_id]["receiving_yards"] += yards
            self.player_stats[target.player_id]["fumbles"] += 1
            self.credit_tackle(defense, defender, yards, play_kind="pass")
            self.player_stats[defender.player_id]["forced_fumbles"] += 1
            if not defense_recovered:
                self.player_stats[target.player_id]["offensive_fumble_recoveries"] += 1
                desc = f"{target.name} catches it for {yards}, then fumbles. {offense.abbreviation} recovers."
                return "normal", yards, 0, 0, desc, offense, int(clamp(new_field, 1, 99)), down, distance, target, defender
            self.team_stats[offense.team_id]["turnovers"] += 1
            self.team_stats[offense.team_id]["fumbles_lost"] += 1
            self.player_stats[target.player_id]["fumbles_lost"] += 1
            self.player_stats[defender.player_id]["fumble_recoveries"] += 1
            self.player_stats[defender.player_id]["fumble_return_yards"] += return_yards
            if return_field >= 100:
                self.add_score(defense, 6)
                self.team_stats[defense.team_id]["defensive_tds"] += 1
                self.player_stats[defender.player_id]["defensive_tds"] += 1
                self.player_stats[defender.player_id]["fumble_return_tds"] += 1
                try_result = self.try_after_touchdown(defense, offense)
                desc = f"{target.name} catches it for {yards}, then fumbles. {defender.name} returns it for a touchdown. {try_result}"
                return "defensive_touchdown", yards, 0, 0, desc, offense, 25, 1, 10, target, defender
            desc = f"{target.name} catches it for {yards}, then fumbles. {defender.name} recovers."
            return "turnover", yards, 0, 0, desc, defense, turnover_field, 1, 10, target, defender

        self.team_stats[offense.team_id]["pass_completions"] += 1
        self.team_stats[offense.team_id]["pass_yards"] += yards
        self.team_stats[offense.team_id]["total_yards"] += yards
        self.player_stats[qb.player_id]["pass_completions"] += 1
        self.player_stats[qb.player_id]["pass_yards"] += yards
        self.player_stats[target.player_id]["receptions"] += 1
        self.player_stats[target.player_id]["receiving_yards"] += yards
        if not touchdown:
            tackler = self.select_pass_tackler(defense, yards, defender, defense_snap_players)
            self.credit_tackle(defense, tackler, yards, play_kind="pass")
            desc = f"{qb.name} completes to {target.name} for {yards}, tackled by {tackler.name}."
        else:
            self.add_score(offense, 6)
            self.team_stats[offense.team_id]["pass_tds"] += 1
            self.player_stats[qb.player_id]["pass_tds"] += 1
            self.player_stats[target.player_id]["receiving_tds"] += 1
            try_result = self.try_after_touchdown(offense, defense)
            desc = f"{qb.name} hits {target.name} for a {max(0, 100 - field_pos)} yard touchdown. {try_result}"
            return "touchdown", yards, 6, 0, desc, self.opponent(offense), 25, 1, 10, target, defender

        return "normal", yards, 0, 0, desc, offense, int(clamp(new_field, 1, 99)), down, distance, target, defender

    def unique_player_pool(self, *groups: list[PlayerSnapshot]) -> list[PlayerSnapshot]:
        players: list[PlayerSnapshot] = []
        seen: set[int] = set()
        for group in groups:
            for player in group:
                if player.player_id in seen or player.player_id in self.injured_player_ids:
                    continue
                seen.add(player.player_id)
                players.append(player)
        return players

    def select_receiver(
        self,
        offense: TeamSnapshot,
        concept: str,
        snap_players: list[PlayerSnapshot] | None = None,
    ) -> PlayerSnapshot:
        pool = []
        if snap_players:
            pool = [
                player
                for player in snap_players
                if player.position in {"WR", "TE", "RB", "FB"} and player.player_id not in self.injured_player_ids
            ]
        if not pool:
            pool = [player for player in offense.receiving_options() if player.player_id not in self.injured_player_ids]
        if not pool:
            pool = offense.receiving_options()

        options = []
        for idx, player in enumerate(pool):
            weight = weighted_average(player, RECEIVER_WEIGHTS)
            if player.position == "WR":
                weight *= 1.15
            if player.position == "TE" and concept in {"short", "intermediate"}:
                weight *= 1.10
            if player.position == "FB":
                weight *= 0.35
            if player.position in {"WR", "TE"}:
                profile = receiver_behavior_profile(player)
                weight *= 1.0 + (profile.target_gravity - 50) * 0.010
                if concept == "quick":
                    weight *= 1.0 + (profile.release_urgency - 50) * 0.006 + (profile.route_pacing - 50) * 0.005
                elif concept in {"short", "intermediate"}:
                    weight *= 1.0 + (profile.middle_comfort - 50) * 0.006 + (profile.route_pacing - 50) * 0.005
                elif concept == "deep":
                    weight *= 1.0 + (profile.vertical_intent - 50) * 0.010 + (profile.sideline_awareness - 50) * 0.004
                elif concept == "screen":
                    weight *= 1.0 + (profile.yac_intent - 50) * 0.007
            if player.position == "RB":
                profile = rb_behavior_profile(player)
                weight *= 1.0 + (profile.pass_game_usage - 50) * 0.010 + (profile.space_creation - 50) * 0.006
                if concept == "screen":
                    weight *= 1.75
            talent_bonus = 1.0 + clamp((weighted_average(player, RECEIVER_WEIGHTS) - 72) * 0.006, -0.06, 0.14)
            weight *= talent_bonus
            weight *= 1.0 / (idx * 0.10 + 1.0)
            options.append((player, weight))
        return weighted_choice(self.rng, options)

    def select_coverage_defender(
        self,
        defense: TeamSnapshot,
        target: PlayerSnapshot,
        concept: str,
        snap_defenders: list[PlayerSnapshot] | None = None,
    ) -> PlayerSnapshot:
        if target.position == "WR":
            slots = ["LCB", "RCB", "NB"] if concept != "deep" else ["LCB", "RCB", "FS", "SS"]
        elif target.position == "TE":
            slots = ["SS", "FS", "MLB", "WLB"]
        else:
            slots = ["MLB", "WLB", "NB", "SS"]
        defenders = defense.unique_starters(slots) or defense.secondary()
        if snap_defenders:
            snap_pool = [
                player
                for player in snap_defenders
                if player.player_id not in self.injured_player_ids
                and player.position in (LB_POSITIONS | SECONDARY_POSITIONS)
            ]
            if target.position == "WR":
                snap_match = [player for player in snap_pool if player.position in SECONDARY_POSITIONS]
            elif target.position in {"TE", "RB", "FB"}:
                snap_match = snap_pool
            else:
                snap_match = snap_pool
            defenders = snap_match or defenders
        weights = []
        for player in defenders:
            weight = weighted_average(player, COVERAGE_WEIGHTS)
            if player.position in LB_POSITIONS:
                profile = lb_behavior_profile(player)
                weight *= 1.0 + (profile.zone_landmark_depth - 50) * 0.006
                weight *= 1.0 + (profile.man_match_carry - 50) * 0.004
                if target.position in {"TE", "RB"}:
                    weight *= 1.0 + (profile.scrape_range - 50) * 0.004
            elif player.position in SECONDARY_POSITIONS:
                profile = secondary_behavior_profile(player)
                if concept == "deep":
                    weight *= 1.0 + (profile.deep_range - 50) * 0.008
                    weight *= 1.0 + (profile.ball_play_timing - 50) * 0.005
                elif concept in {"quick", "short"}:
                    weight *= 1.0 + (profile.break_trigger - 50) * 0.006
                    weight *= 1.0 + (profile.slot_traffic - 50) * 0.004
                else:
                    weight *= 1.0 + (profile.man_mirror - 50) * 0.005
                    weight *= 1.0 + (profile.zone_eye_discipline - 50) * 0.005
                if target.position == "WR":
                    weight *= 1.0 + (profile.press_timing - 50) * 0.003
                elif target.position in {"TE", "RB"}:
                    weight *= 1.0 + (profile.run_support_fit - 50) * 0.003
            weights.append((player, weight))
        return weighted_choice(self.rng, weights)

    def select_pass_rusher(self, defense: TeamSnapshot, rushers: list[PlayerSnapshot] | None = None) -> PlayerSnapshot:
        rushers = rushers or defense.defensive_front() or defense.roster[:5]
        if self.rng.random() < 0.64:
            cleanup_pool = []
            used = set()
            for player in [*defense.linebackers(), *defense.secondary()]:
                if player.player_id in used:
                    continue
                cleanup_pool.append(player)
                used.add(player.player_id)
            if cleanup_pool:
                return weighted_choice(
                    self.rng,
                    [(p, max(1.0, sack_credit_weight(p)) ** 0.55) for p in cleanup_pool],
                )
        weights = []
        for player in rushers:
            weight = sack_credit_weight(player)
            current_sacks = self.player_stats[player.player_id].get("sacks", 0)
            if current_sacks:
                score = weighted_average(player, SACK_CREDIT_WEIGHTS)
                if current_sacks == 1:
                    if score >= 87:
                        weight *= 0.55
                    elif score >= 84:
                        weight *= 0.38
                    else:
                        weight *= 0.22
                else:
                    if score >= 87:
                        weight *= 0.16
                    elif score >= 84:
                        weight *= 0.08
                    else:
                        weight *= 0.04
            weights.append((player, weight))
        return weighted_choice(self.rng, weights)

    def fumble_event_chance(
        self,
        ball_carrier: PlayerSnapshot,
        defender: PlayerSnapshot,
        defense: TeamSnapshot,
        yards: int,
        *,
        play_kind: str,
    ) -> float:
        security = average(
            [
                ball_carrier.rating("ball_security"),
                ball_carrier.rating("balance"),
                ball_carrier.rating("composure"),
            ]
        )
        contact = average(
            [
                defender.rating("forced_fumble"),
                defender.rating("hit_power"),
                defender.rating("tackle_wrap"),
            ]
        )
        chance = {
            "run": 0.0215,
            "scramble": 0.0275,
            "pass": 0.0155,
            "sack": 0.0480,
        }.get(play_kind, 0.0160)
        chance += (contact - security) * 0.00055
        chance += (defense.tackling_score() - 68) * 0.00008
        if ball_carrier.position in {"RB", "FB"}:
            profile = rb_behavior_profile(ball_carrier)
            chance -= (profile.ball_security_mindset - 50) * 0.00018
            chance += max(0.0, profile.contact_appetite - 78) * 0.00010
        if ball_carrier.position in {"WR", "TE"}:
            profile = receiver_behavior_profile(ball_carrier)
            chance -= (profile.catch_security - 50) * 0.00015
            chance += max(0.0, profile.yac_intent - 78) * 0.00008
        if defender.position in SECONDARY_POSITIONS:
            profile = secondary_behavior_profile(defender)
            chance += (profile.tackle_finish - 50) * 0.00008
            chance += max(0.0, profile.run_support_fit - 82) * 0.00008
            chance += max(0.0, profile.catch_point_compete - 84) * 0.00005
        if yards <= 2:
            chance += 0.0030
        elif yards >= 12:
            chance += 0.0020
        if play_kind == "sack":
            chance += max(0, defender.rating("sack_finish") - 70) * 0.00022
            chance += max(0, 72 - ball_carrier.rating("ball_security")) * 0.00022
        if ball_carrier.position == "QB" and play_kind in {"scramble", "sack"}:
            chance += 0.0040
        return clamp(chance, 0.0040, 0.0700 if play_kind == "sack" else 0.0460)

    def defense_fumble_recovery_chance(
        self,
        ball_carrier: PlayerSnapshot,
        defender: PlayerSnapshot,
        yards: int,
        *,
        play_kind: str,
    ) -> float:
        chance = 0.455
        chance += (defender.rating("play_recognition") - ball_carrier.rating("composure")) * 0.0010
        chance += (defender.rating("forced_fumble") - ball_carrier.rating("ball_security")) * 0.0008
        if defender.position in SECONDARY_POSITIONS:
            profile = secondary_behavior_profile(defender)
            chance += (profile.break_trigger - 50) * 0.0006
            chance += (profile.run_support_fit - 50) * 0.0005
        if play_kind == "sack":
            chance += 0.070
        elif play_kind == "pass" and yards >= 10:
            chance -= 0.025
        elif play_kind in {"run", "scramble"} and yards <= 2:
            chance += 0.030
        return clamp(chance, 0.350, 0.640)

    def credit_tackle(
        self,
        defense: TeamSnapshot,
        primary: PlayerSnapshot,
        yards: int,
        *,
        play_kind: str,
        force_solo: bool = False,
    ) -> None:
        assisted = False if force_solo else self.rng.random() < self.assisted_tackle_chance(primary, yards, play_kind)
        if assisted:
            self.player_stats[primary.player_id]["assisted_tackles"] += 1
        else:
            self.player_stats[primary.player_id]["solo_tackles"] += 1
        self.player_stats[primary.player_id]["tackles"] += 1

        if not assisted:
            return

        used = {primary.player_id}
        assist_count = 1 + (1 if self.rng.random() < 0.025 else 0)
        for _idx in range(assist_count):
            helper = self.select_assist_tackler(defense, yards, play_kind, used)
            if not helper:
                return
            used.add(helper.player_id)
            self.player_stats[helper.player_id]["assisted_tackles"] += 1
            self.player_stats[helper.player_id]["tackles"] += 1

    def assisted_tackle_chance(self, primary: PlayerSnapshot, yards: int, play_kind: str) -> float:
        if play_kind == "sack":
            return 0.0

        chance = 0.270
        if play_kind == "run":
            chance += 0.040
        elif play_kind == "pass":
            chance -= 0.055
        elif play_kind == "scramble":
            chance -= 0.005

        if yards <= 2:
            chance += 0.055
        elif yards <= 6:
            chance += 0.010
        elif yards >= 15:
            chance -= 0.105

        chance -= (primary.rating("solo_tackle") - 70) * 0.0022
        chance += (primary.rating("assist_tackle") - 70) * 0.0012
        chance -= (primary.rating("open_field_tackle") - 70) * 0.0009
        if primary.position in LB_POSITIONS:
            profile = lb_behavior_profile(primary)
            chance -= (profile.tackle_finish - 50) * 0.0010
            chance += (profile.rally_support - 50) * 0.0012
        elif primary.position in SECONDARY_POSITIONS:
            profile = secondary_behavior_profile(primary)
            chance -= 0.030
            chance -= (profile.tackle_finish - 50) * 0.0010
            chance += (profile.run_support_fit - 50) * 0.0004
            if play_kind == "pass":
                chance += (profile.slot_traffic - 50) * 0.0003
        return clamp(chance, 0.100, 0.480)

    def select_assist_tackler(
        self,
        defense: TeamSnapshot,
        yards: int,
        play_kind: str,
        exclude_player_ids: set[int],
    ) -> PlayerSnapshot | None:
        if play_kind == "pass":
            if yards <= 5:
                pool = defense.linebackers() + defense.secondary()
            elif yards <= 14:
                pool = defense.secondary() + defense.linebackers()
            else:
                pool = defense.secondary()
        elif play_kind == "scramble":
            pool = defense.linebackers() + defense.secondary()
        else:
            if yards <= 2:
                pool = defense.linebackers() + defense.unique_starters(["LDL", "NT", "RDL", "SS"])
            elif yards <= 8:
                pool = defense.linebackers() + defense.unique_starters(["SS", "FS", "NB"])
            else:
                pool = defense.secondary() + defense.linebackers()

        pool = [player for player in (pool or defense.roster[:11]) if player.player_id not in exclude_player_ids]
        if not pool:
            return None
        weights = []
        for player in pool:
            weight = weighted_average(player, ASSIST_TACKLE_WEIGHTS)
            if player.position in {"LB", "ILB", "OLB"}:
                weight *= 1.16
                profile = lb_behavior_profile(player)
                weight *= 1.0 + (profile.rally_support - 50) * 0.006
                weight *= 1.0 + (profile.scrape_range - 50) * 0.003
            if play_kind in {"run", "scramble"} and player.position in {"EDGE", "DE"}:
                weight *= 0.32
            if play_kind == "run" and yards <= 2 and player.position in {"IDL", "DT", "NT"}:
                weight *= 1.05
            if play_kind == "run" and yards <= 2 and player.position in {"LB", "ILB"}:
                weight *= 1.12
            if play_kind in {"run", "scramble"} and 3 <= yards <= 10 and player.position in {"S", "FS", "SS", "NB"}:
                weight *= 0.86
                profile = secondary_behavior_profile(player)
                weight *= 1.0 + (profile.run_support_fit - 50) * 0.004
                weight *= 1.0 + (profile.slot_traffic - 50) * 0.003
            if yards >= 10 and player.position in {"CB", "S", "FS", "SS"}:
                weight *= 0.94
                profile = secondary_behavior_profile(player)
                weight *= 1.0 + (profile.deep_range - 50) * 0.004
                weight *= 1.0 + (profile.break_trigger - 50) * 0.003
            weights.append((player, weight))
        return weighted_choice(self.rng, weights)

    def select_tackler(self, defense: TeamSnapshot, yards: int) -> PlayerSnapshot:
        if yards <= 4:
            pool = defense.defensive_front() + defense.linebackers()
        elif yards <= 14:
            pool = defense.linebackers() + defense.secondary()
        else:
            pool = defense.secondary() + defense.linebackers()
        pool = pool or defense.roster[:11]
        weights = []
        for player in pool:
            weight = weighted_average(player, TACKLE_WEIGHTS)
            if player.position in LB_POSITIONS:
                profile = lb_behavior_profile(player)
                weight *= 1.0 + (profile.tackle_finish - 50) * 0.004
                weight *= 1.0 + (profile.scrape_range - 50) * 0.003
                weight *= 1.0 + (profile.trigger_quickness - 50) * 0.002
            elif player.position in SECONDARY_POSITIONS:
                profile = secondary_behavior_profile(player)
                weight *= 1.0 + (profile.tackle_finish - 50) * 0.004
                weight *= 1.0 + (profile.run_support_fit - 50) * 0.003
                if yards >= 8:
                    weight *= 1.0 + (profile.deep_range - 50) * 0.003
            weights.append((player, weight))
        return weighted_choice(self.rng, weights)

    def select_run_tackler(
        self,
        defense: TeamSnapshot,
        yards: int,
        snap_defenders: list[PlayerSnapshot] | None = None,
    ) -> PlayerSnapshot:
        if snap_defenders:
            snap_pool = [player for player in snap_defenders if player.player_id not in self.injured_player_ids]
            interior = [player for player in snap_pool if player.position in {"IDL", "DT", "NT"}]
            edge = [player for player in snap_pool if player.position in {"EDGE", "OLB", "DE"}]
            linebackers = [player for player in snap_pool if player.position in LB_POSITIONS]
            secondary = [player for player in snap_pool if player.position in SECONDARY_POSITIONS]
            if yards < 0:
                pool = self.unique_player_pool(interior, linebackers, edge)
            elif yards <= 2:
                pool = self.unique_player_pool(linebackers, interior, secondary)
            elif yards <= 7:
                pool = self.unique_player_pool(linebackers, secondary, interior)
            elif yards <= 14:
                pool = self.unique_player_pool(linebackers, secondary)
            else:
                pool = self.unique_player_pool(secondary, linebackers)
        elif yards < 0:
            pool = defense.unique_starters(["LDL", "NT", "RDL", "MLB", "WLB", "SLB", "LEDGE", "REDGE"])
        elif yards <= 2:
            pool = defense.unique_starters(["MLB", "WLB", "SLB", "LDL", "NT", "RDL", "SS"])
        elif yards <= 7:
            pool = defense.linebackers() + defense.unique_starters(["SS", "FS", "NB", "LDL", "NT", "RDL"])
        elif yards <= 14:
            pool = defense.linebackers() + defense.secondary()
        else:
            pool = defense.secondary() + defense.linebackers()
        pool = pool or defense.roster[:11]
        weights = []
        for player in pool:
            weight = weighted_average(player, TACKLE_WEIGHTS)
            if yards < 0 and player.position in {"EDGE", "OLB", "DE"}:
                weight *= 0.85
            elif yards <= 7 and player.position in {"EDGE", "DE"}:
                weight *= 0.30
            if yards <= 2 and player.position in {"IDL", "DT", "NT"}:
                weight *= 0.95
                profile = idl_behavior_profile(player)
                weight *= 1.0 + (average([profile.gap_control, profile.block_shed_timing]) - 50) * 0.004
            if yards <= 7 and player.position in {"LB", "ILB", "OLB"}:
                weight *= 1.25
                profile = lb_behavior_profile(player)
                weight *= 1.0 + (profile.trigger_quickness - 50) * 0.004
                weight *= 1.0 + (profile.scrape_range - 50) * 0.004
                weight *= 1.0 + (profile.gap_fit_discipline - 50) * 0.003
                weight *= 1.0 + (profile.tackle_finish - 50) * 0.003
            if 3 <= yards <= 10 and player.position in {"S", "FS", "SS", "NB"}:
                weight *= 0.58
                profile = secondary_behavior_profile(player)
                weight *= 1.0 + (profile.run_support_fit - 50) * 0.003
                weight *= 1.0 + (profile.tackle_finish - 50) * 0.003
                weight *= 1.0 + (profile.slot_traffic - 50) * 0.002
            if 3 <= yards <= 7 and player.position in {"IDL", "DT", "NT"}:
                weight *= 0.82
                profile = idl_behavior_profile(player)
                weight *= 1.0 + (profile.double_team_anchor - 50) * 0.002
            if yards >= 8 and player.position in {"CB", "S", "FS", "SS"}:
                weight *= 0.66
                profile = secondary_behavior_profile(player)
                weight *= 1.0 + (profile.deep_range - 50) * 0.004
                weight *= 1.0 + (profile.break_trigger - 50) * 0.003
                weight *= 1.0 + (profile.tackle_finish - 50) * 0.003
            current_tackles = self.player_stats[player.player_id].get("tackles", 0)
            if current_tackles >= 7:
                weight *= 0.10
            elif current_tackles >= 5:
                weight *= 0.22
            elif current_tackles >= 3:
                weight *= 0.48
            weights.append((player, weight))
        return weighted_choice(self.rng, weights)

    def select_pass_tackler(
        self,
        defense: TeamSnapshot,
        yards: int,
        coverage_defender: PlayerSnapshot | None,
        snap_defenders: list[PlayerSnapshot] | None = None,
    ) -> PlayerSnapshot:
        if coverage_defender and coverage_defender.position not in {"EDGE", "DE", "IDL", "DT", "NT", "OL"}:
            if self.rng.random() < clamp(0.22 - max(0, yards - 8) * 0.008, 0.10, 0.34):
                return coverage_defender
        if snap_defenders:
            snap_pool = [player for player in snap_defenders if player.player_id not in self.injured_player_ids]
            linebackers = [player for player in snap_pool if player.position in LB_POSITIONS]
            secondary = [player for player in snap_pool if player.position in SECONDARY_POSITIONS]
            if yards <= 5:
                pool = self.unique_player_pool(linebackers, secondary)
            elif yards <= 14:
                pool = self.unique_player_pool(linebackers, secondary)
            else:
                pool = self.unique_player_pool(secondary, linebackers)
        elif yards <= 5:
            pool = defense.linebackers() + defense.secondary()
        elif yards <= 14:
            pool = defense.linebackers() + defense.secondary()
        else:
            pool = defense.secondary() + defense.linebackers()
        pool = pool or defense.linebackers() or defense.roster[:11]
        weights = []
        for player in pool:
            weight = weighted_average(player, TACKLE_WEIGHTS)
            if player.position in LB_POSITIONS:
                profile = lb_behavior_profile(player)
                weight *= 1.20
                weight *= 1.0 + (profile.tackle_finish - 50) * 0.004
                weight *= 1.0 + (profile.rally_support - 50) * 0.003
                weight *= 1.0 + (profile.scrape_range - 50) * 0.002
            elif player.position in SECONDARY_POSITIONS:
                profile = secondary_behavior_profile(player)
                weight *= 0.52 if yards <= 14 else 0.68
                weight *= 1.0 + (profile.tackle_finish - 50) * 0.004
                weight *= 1.0 + (profile.slot_traffic - 50) * 0.003
                if yards >= 10:
                    weight *= 1.0 + (profile.deep_range - 50) * 0.003
            current_tackles = self.player_stats[player.player_id].get("tackles", 0)
            if current_tackles >= 7:
                weight *= 0.10
            elif current_tackles >= 5:
                weight *= 0.22
            elif current_tackles >= 3:
                weight *= 0.48
            weights.append((player, weight))
        return weighted_choice(self.rng, weights)

    def select_returner(self, team: TeamSnapshot, play_type: str) -> PlayerSnapshot:
        positions = {"RB", "WR", "CB", "NB", "FS", "SS", "S"}
        if play_type == "punt":
            positions |= {"SWR"}
        preferred = []
        fallback = []
        for player in team.roster:
            if player.player_id in self.injured_player_ids or player.position not in positions:
                continue
            source = str(player.metadata.get("specialist_behavior_source") or "")
            has_stored_st_profile = source.startswith("specialist_behavior_") or source == "draft_selection"
            if has_stored_st_profile or player.general_score() <= 82:
                preferred.append(player)
            fallback.append(player)
        pool = preferred or fallback or team.receiving_options() or team.roster[:5]
        return weighted_choice(
            self.rng,
            [
                (
                    player,
                    special_teams_return_weight(player),
                )
                for player in pool
            ],
        )

    def try_after_touchdown(self, offense: TeamSnapshot, defense: TeamSnapshot) -> str:
        diff = self.current_score_diff(offense)
        late = self.quarter >= 4 or self.quarter == 5
        go_for_two = False
        if late and diff in {-2, 1, 5, 10}:
            go_for_two = True
        elif late and diff < 0 and abs(diff) in {2, 5, 10, 13}:
            go_for_two = True
        elif self.rng.random() < 0.012:
            go_for_two = True

        if not go_for_two:
            return self.extra_point(offense)

        qb = self.active_starter(offense, "QB")
        runner = self.active_starter(offense, "RB")
        pass_try = self.rng.random() < 0.58
        snap_play_type = "pass" if pass_try else "run"
        snap_concept = "short" if pass_try else "power"
        offense_snap_players = self.offensive_snap_players(
            offense,
            snap_concept,
            snap_play_type,
            ball_carrier=None if pass_try else runner,
        )
        defense_snap_players = self.defensive_snap_players(defense, snap_play_type, snap_concept)
        self.set_snap_override(offense, "offensive_snaps", offense_snap_players)
        self.set_snap_override(defense, "defensive_snaps", defense_snap_players)
        target = self.select_receiver(offense, "short", offense_snap_players) if pass_try else None
        self.count_scrimmage_snap(offense, defense, snap_play_type, snap_concept)
        self.team_stats[offense.team_id]["two_point_attempts"] += 1
        self.player_stats[qb.player_id if pass_try else runner.player_id]["two_point_attempts"] += 1

        offense_score = average(
            [
                weighted_average(qb, QB_PASS_WEIGHTS) if pass_try else weighted_average(runner, RB_RUN_WEIGHTS),
                weighted_average(target, RECEIVER_WEIGHTS) if pass_try and target else offense.run_block_score(),
                offense.discipline_score(),
            ]
        )
        defense_score = average([defense.coverage_score() if pass_try else defense.run_defense_score(), defense.tackling_score()])
        make_chance = clamp(0.465 + (offense_score - defense_score) * 0.004, 0.280, 0.680)
        if self.rng.random() < make_chance:
            self.add_score(offense, 2)
            self.team_stats[offense.team_id]["two_point_made"] += 1
            if pass_try:
                self.player_stats[qb.player_id]["two_point_passes"] += 1
                if target:
                    self.player_stats[target.player_id]["two_point_conversions"] += 1
                    return f"{qb.name} converts the two-point try to {target.name}."
                return f"{qb.name} converts the two-point try."
            self.player_stats[runner.player_id]["two_point_conversions"] += 1
            return f"{runner.name} runs in the two-point try."
        return "Two-point try failed."

    def extra_point(self, offense: TeamSnapshot) -> str:
        kicker = offense.starter("PK")
        real_kicker = kicker.position in KICKER_POSITIONS
        operation_score = self.kicking_operation_score(offense, "extra_point")
        if real_kicker:
            make = clamp(
                0.925
                + (weighted_average(kicker, KICK_WEIGHTS) - 65) * 0.0017
                + (operation_score - 65) * 0.0011,
                0.82,
                0.995,
            )
        else:
            make = 0.62
        self.count_special_teams_snap(offense, "extra_point")
        self.team_stats[offense.team_id]["xp_attempts"] += 1
        self.player_stats[kicker.player_id]["xp_attempts"] += 1
        if self.rng.random() < make:
            self.add_score(offense, 1)
            self.team_stats[offense.team_id]["xp_made"] += 1
            self.player_stats[kicker.player_id]["xp_made"] += 1
            return "Extra point good."
        return "Extra point no good."

    def field_goal(self, offense: TeamSnapshot, defense: TeamSnapshot, field_pos: int) -> tuple[str, TeamSnapshot, int, str]:
        kicker = offense.starter("PK")
        distance = int(clamp(round(100 - field_pos + 17), 18, 69))
        real_kicker = kicker.position in KICKER_POSITIONS
        kick_score = weighted_average(kicker, KICK_WEIGHTS) if real_kicker else 42.0
        operation_score = self.kicking_operation_score(offense, "field_goal")
        if real_kicker:
            make = 0.985 - max(0, distance - 28) * 0.014 + (kick_score - 65) * 0.0024 + (operation_score - 65) * 0.0012
            make -= max(0, distance - 54) * 0.035
            make -= max(0, distance - 60) * 0.055
            make -= max(0, distance - 65) * 0.050
            make = clamp(make, 0.025 if distance >= 64 else 0.10 if distance >= 61 else 0.18, 0.985)
        else:
            make = 0.58 - max(0, distance - 25) * 0.026
            make = clamp(make, 0.01, 0.58)
        self.team_stats[offense.team_id]["fg_attempts"] += 1
        self.player_stats[kicker.player_id]["fg_attempts"] += 1
        block_score = self.special_teams_block_score(defense, "punt_return")
        block_chance = clamp(
            0.010
            + (72 - kick_score) * 0.00014
            + (68 - operation_score) * 0.00010
            + (block_score - 70) * 0.00008,
            0.004,
            0.026,
        )
        if self.rng.random() < block_chance:
            blocker = self.select_special_teams_blocker(defense, "punt_return")
            return_yards = max(0, int(round(self.rng.gauss(8, 9))))
            return_field = 100 - field_pos + return_yards
            self.team_stats[defense.team_id]["blocked_kicks"] += 1
            self.player_stats[blocker.player_id]["blocked_kicks"] += 1
            self.player_stats[blocker.player_id]["field_goal_return_yards"] += return_yards
            if return_field >= 100:
                self.add_score(defense, 6)
                self.team_stats[defense.team_id]["special_teams_tds"] += 1
                self.player_stats[blocker.player_id]["special_teams_tds"] += 1
                try_result = self.try_after_touchdown(defense, offense)
                return "field_goal_return_touchdown", offense, 25, f"{blocker.name} blocks the field goal and returns it for a touchdown. {try_result}"
            return "blocked_field_goal", defense, int(clamp(return_field, 1, 99)), f"{blocker.name} blocks the {distance} yard field goal."
        if self.rng.random() < make:
            self.add_score(offense, 3)
            self.team_stats[offense.team_id]["fg_made"] += 1
            self.player_stats[kicker.player_id]["fg_made"] += 1
            self.player_stats[kicker.player_id]["long_fg"] = max(self.player_stats[kicker.player_id]["long_fg"], distance)
            return "field_goal", defense, 25, f"{kicker.name} makes a {distance} yard field goal."
        new_field = 25 if distance >= 56 else int(clamp(100 - field_pos, 20, 99))
        return "missed_field_goal", defense, new_field, f"{kicker.name} misses a {distance} yard field goal."

    def punt(self, offense: TeamSnapshot, defense: TeamSnapshot, field_pos: int) -> tuple[str, TeamSnapshot, int, str, PlayerSnapshot | None]:
        punter = offense.starter("PT")
        returner = self.select_returner(defense, "punt")
        punt_score = weighted_average(punter, PUNT_WEIGHTS)
        operation_score = self.kicking_operation_score(offense, "punt")
        block_score = self.special_teams_block_score(defense, "punt_return")
        punter_profile = specialist_behavior_profile(punter)
        block_chance = clamp(
            0.012
            + (68 - operation_score) * 0.00008
            + (block_score - 70) * 0.00008
            - (punt_score - 65) * 0.00005,
            0.004,
            0.030,
        )
        if self.rng.random() < block_chance:
            blocker = self.select_special_teams_blocker(defense, "punt_return")
            return_yards = max(0, int(round(self.rng.gauss(7, 8))))
            return_field = 100 - field_pos + return_yards
            self.team_stats[offense.team_id]["punts"] += 1
            self.player_stats[punter.player_id]["punts"] += 1
            self.team_stats[defense.team_id]["blocked_punts"] += 1
            self.player_stats[blocker.player_id]["blocked_punts"] += 1
            self.player_stats[blocker.player_id]["punt_return_yards"] += return_yards
            if return_field >= 100:
                self.add_score(defense, 6)
                self.team_stats[defense.team_id]["special_teams_tds"] += 1
                self.player_stats[blocker.player_id]["special_teams_tds"] += 1
                self.player_stats[blocker.player_id]["punt_return_tds"] += 1
                try_result = self.try_after_touchdown(defense, offense)
                return "punt_return_touchdown", offense, 25, f"{blocker.name} blocks the punt and returns it for a touchdown. {try_result}", blocker
            return "blocked_punt", defense, int(clamp(return_field, 1, 99)), f"{blocker.name} blocks the punt.", blocker

        gross = int(clamp(round(self.rng.gauss(43 + (punt_score - 60) * 0.10 + (punter_profile.punt_hang_time - 60) * 0.045, 7)), 22, 70))
        absolute_landing = field_pos + gross
        self.team_stats[offense.team_id]["punts"] += 1
        self.team_stats[offense.team_id]["punt_yards"] += gross
        self.player_stats[punter.player_id]["punts"] += 1
        self.player_stats[punter.player_id]["punt_yards"] += gross
        if absolute_landing >= 100:
            return "punt", defense, 20, f"{punter.name} punts {gross} yards for a touchback.", None
        coverage_score = self.special_teams_coverage_score(offense, "punt")
        fair_catch_chance = clamp(
            0.12
            + max(0, absolute_landing - 70) * 0.010
            + (punt_score - 65) * 0.0012
            + (punter_profile.punt_placement - 60) * 0.0012
            + (coverage_score - 68) * 0.0010,
            0.08,
            0.55,
        )
        if self.rng.random() < fair_catch_chance:
            opponent_field = int(clamp(100 - absolute_landing, 1, 99))
            self.team_stats[defense.team_id]["fair_catches"] += 1
            self.player_stats[returner.player_id]["fair_catches"] += 1
            return "punt", defense, opponent_field, f"{punter.name} punts {gross} yards. {returner.name} fair catches it.", returner

        return_score = special_teams_return_weight(returner)
        return_yards = max(
            0,
            int(
                round(
                    self.rng.gauss(
                        7
                        - (punt_score - 60) * 0.016
                        - (coverage_score - 68) * 0.035
                        - (punter_profile.punt_hang_time - 60) * 0.018
                        + (return_score - 65) * 0.045,
                        6,
                    )
                )
            ),
        )
        return_field = 100 - absolute_landing + return_yards
        opponent_field = int(clamp(return_field, 1, 99))
        self.team_stats[defense.team_id]["punt_returns"] += 1
        self.team_stats[defense.team_id]["punt_return_yards"] += return_yards
        self.player_stats[returner.player_id]["punt_returns"] += 1
        self.player_stats[returner.player_id]["punt_return_yards"] += return_yards
        if return_field >= 100:
            self.add_score(defense, 6)
            self.team_stats[defense.team_id]["special_teams_tds"] += 1
            self.player_stats[returner.player_id]["special_teams_tds"] += 1
            self.player_stats[returner.player_id]["punt_return_tds"] += 1
            try_result = self.try_after_touchdown(defense, offense)
            return "punt_return_touchdown", offense, 25, f"{returner.name} returns {punter.name}'s punt for a touchdown. {try_result}", returner
        return "punt", defense, opponent_field, f"{punter.name} punts {gross} yards. {returner.name} returns it {return_yards}.", returner

    def record_free_kick(
        self,
        kicking_team: TeamSnapshot,
        receiving_team: TeamSnapshot,
        *,
        concept: str,
        start_yardline: int,
        yards: int,
        live_tenths: int,
        description: str,
        returner: PlayerSnapshot | None = None,
        touchdown: bool = False,
        turnover: bool = False,
    ) -> None:
        self.count_special_teams_play(kicking_team, receiving_team, "safety_kick" if concept == "safety_kick" else "kickoff")
        if live_tenths > 0:
            consumed, runoff = self.consume_clock(live_tenths, 0)
        else:
            consumed, runoff = 0, 0
        self.play_number += 1
        self.add_play_event(
            PlayEvent(
                play_number=self.play_number,
                drive_number=0,
                quarter=self.quarter,
                clock_tenths=self.clock_tenths,
                offense_team_id=kicking_team.team_id,
                defense_team_id=receiving_team.team_id,
                down=0,
                distance=0,
                yardline=start_yardline,
                play_type="kickoff",
                concept=concept,
                yards_gained=yards,
                offense_player_id=kicking_team.starter("KO").player_id if kicking_team.starter("KO") else None,
                target_player_id=returner.player_id if returner else None,
                defense_player_id=returner.player_id if returner else None,
                is_touchdown=1 if touchdown else 0,
                is_turnover=1 if turnover else 0,
                clock_elapsed_tenths=consumed,
                runoff_tenths=runoff,
                description=description,
            )
        )
        if returner and live_tenths > 0:
            coverage_player = self.select_special_teams_coverage_player(kicking_team, "kickoff")
            self.consider_injury(
                returner,
                receiving_team,
                opponent_player=coverage_player,
                opponent_team=kicking_team if coverage_player else None,
                play_type="kickoff",
                mechanism="special_teams",
                high_impact=touchdown or yards >= 35,
            )
            if coverage_player:
                self.consider_injury(
                    coverage_player,
                    kicking_team,
                    opponent_player=returner,
                    opponent_team=receiving_team,
                    play_type="kickoff",
                    mechanism="special_teams",
                    high_impact=touchdown or yards >= 35,
                )

    def kickoff(
        self,
        kicking_team: TeamSnapshot,
        receiving_team: TeamSnapshot,
        *,
        reason: str = "kickoff",
        safety_kick: bool = False,
        onside: bool = False,
    ) -> tuple[str, TeamSnapshot, int]:
        kicker = kicking_team.starter("KO") or kicking_team.starter("PK")
        returner = self.select_returner(receiving_team, "kickoff")
        kick_score = weighted_average(kicker, KICK_WEIGHTS)
        kicker_profile = specialist_behavior_profile(kicker)
        kickoff_control = average([kicker_profile.kickoff_control, kick_score])
        start_yardline = 20 if safety_kick else 35
        concept = "safety_kick" if safety_kick else "onside_kick" if onside else reason
        self.team_stats[kicking_team.team_id]["kickoffs"] += 1
        self.player_stats[kicker.player_id]["kickoffs"] += 1

        if onside:
            recovery_chance = clamp(
                0.115
                + (kick_score - 65) * 0.0010
                + (kicker_profile.kick_operation - 65) * 0.0007
                - (receiving_team.discipline_score() - 65) * 0.0012,
                0.045,
                0.210,
            )
            recovered = self.rng.random() < recovery_chance
            self.team_stats[kicking_team.team_id]["onside_kicks"] += 1
            if recovered:
                self.team_stats[kicking_team.team_id]["onside_recoveries"] += 1
                self.record_free_kick(kicking_team, receiving_team, concept=concept, start_yardline=start_yardline, yards=11, live_tenths=18, description=f"{kicking_team.abbreviation} recovers the onside kick.", turnover=True)
                return "recovered", kicking_team, 46
            self.record_free_kick(kicking_team, receiving_team, concept=concept, start_yardline=start_yardline, yards=10, live_tenths=14, description=f"{receiving_team.abbreviation} covers the onside kick.", returner=returner)
            return "normal", receiving_team, 54

        bad_kick_adjust = clamp((68 - kickoff_control) * 0.0005, -0.010, 0.018)
        out_of_bounds_threshold = clamp(0.035 + bad_kick_adjust, 0.018, 0.055)
        short_threshold = clamp(out_of_bounds_threshold + 0.040 + bad_kick_adjust, out_of_bounds_threshold + 0.022, 0.100)
        end_zone_threshold = clamp(short_threshold + 0.180 + (kickoff_control - 65) * 0.0015, short_threshold + 0.105, 0.335)
        landing_down_threshold = clamp(end_zone_threshold + 0.070 + (kicker_profile.kick_operation - 65) * 0.0009, end_zone_threshold + 0.040, 0.410)

        roll = self.rng.random()
        if roll < out_of_bounds_threshold:
            self.team_stats[kicking_team.team_id]["kickoffs_out_of_bounds"] += 1
            self.record_free_kick(kicking_team, receiving_team, concept=concept, start_yardline=start_yardline, yards=25, live_tenths=0, description=f"{kicker.name}'s kickoff is out of bounds. {receiving_team.abbreviation} starts at the 40.")
            return "normal", receiving_team, 40
        if roll < short_threshold:
            self.team_stats[kicking_team.team_id]["kickoffs_short"] += 1
            self.record_free_kick(kicking_team, receiving_team, concept=concept, start_yardline=start_yardline, yards=18, live_tenths=0, description=f"{kicker.name}'s kickoff lands short of the landing zone. {receiving_team.abbreviation} starts at the 40.")
            return "normal", receiving_team, 40
        if roll < end_zone_threshold:
            self.team_stats[kicking_team.team_id]["kickoff_touchbacks"] += 1
            self.record_free_kick(kicking_team, receiving_team, concept=concept, start_yardline=start_yardline, yards=65, live_tenths=0, description=f"{kicker.name}'s kickoff reaches the end zone for a touchback. {receiving_team.abbreviation} starts at the 35.")
            return "normal", receiving_team, 35
        if roll < landing_down_threshold:
            self.team_stats[kicking_team.team_id]["kickoff_touchbacks"] += 1
            self.record_free_kick(kicking_team, receiving_team, concept=concept, start_yardline=start_yardline, yards=60, live_tenths=0, description=f"{kicker.name}'s kickoff lands in the landing zone and is downed. {receiving_team.abbreviation} starts at the 20.")
            return "normal", receiving_team, 20

        return_score = special_teams_return_weight(returner)
        coverage_score = average([self.special_teams_coverage_score(kicking_team, "kickoff"), kicking_team.discipline_score()])
        caught_at = int(clamp(round(self.rng.gauss(5, 4)), 0, 20))
        return_yards = max(0, int(round(self.rng.gauss(24 + (return_score - coverage_score) * 0.075 - (kickoff_control - 65) * 0.020, 8))))
        return_field = caught_at + return_yards
        self.team_stats[receiving_team.team_id]["kickoff_returns"] += 1
        self.team_stats[receiving_team.team_id]["kickoff_return_yards"] += return_yards
        self.player_stats[returner.player_id]["kickoff_returns"] += 1
        self.player_stats[returner.player_id]["kickoff_return_yards"] += return_yards
        if return_field >= 100:
            self.add_score(receiving_team, 6)
            if self.quarter == 5:
                self.ot_possessions.add(receiving_team.team_id)
            self.team_stats[receiving_team.team_id]["special_teams_tds"] += 1
            self.player_stats[returner.player_id]["special_teams_tds"] += 1
            self.player_stats[returner.player_id]["kickoff_return_tds"] += 1
            try_result = self.try_after_touchdown(receiving_team, kicking_team)
            desc = f"{returner.name} returns {kicker.name}'s kickoff for a touchdown. {try_result}"
            self.record_free_kick(kicking_team, receiving_team, concept=concept, start_yardline=start_yardline, yards=100, live_tenths=85, description=desc, returner=returner, touchdown=True)
            return "touchdown", kicking_team, 25

        field_pos = int(clamp(return_field, 1, 99))
        desc = f"{returner.name} returns {kicker.name}'s kickoff {return_yards} yards to {format_yardline(field_pos)}."
        self.record_free_kick(kicking_team, receiving_team, concept=concept, start_yardline=start_yardline, yards=return_yards, live_tenths=int(clamp(round(self.rng.gauss(58, 11)), 35, 95)), description=desc, returner=returner)
        return "normal", receiving_team, field_pos

    def record_play(
        self,
        drive_number: int,
        offense: TeamSnapshot,
        defense: TeamSnapshot,
        down: int,
        distance: int,
        field_pos: int,
        play_type: str,
        concept: str,
        yards: int,
        live_tenths: int,
        runoff_tenths: int,
        description: str,
        offense_player: PlayerSnapshot | None = None,
        target_player: PlayerSnapshot | None = None,
        defense_player: PlayerSnapshot | None = None,
        touchdown: bool = False,
        turnover: bool = False,
    ) -> None:
        if play_type in {"run", "pass"}:
            self.count_scrimmage_snap(
                offense,
                defense,
                play_type,
                concept,
                offense_player=offense_player,
                target_player=target_player,
            )
        elif play_type in {"field_goal", "punt"}:
            if play_type == "punt":
                self.count_special_teams_play(offense, defense, "punt")
            else:
                self.count_special_teams_snap(offense, play_type)
        self.play_number += 1
        self.add_play_event(
            PlayEvent(
                play_number=self.play_number,
                drive_number=drive_number,
                quarter=self.quarter,
                clock_tenths=self.clock_tenths,
                offense_team_id=offense.team_id,
                defense_team_id=defense.team_id,
                down=down,
                distance=distance,
                yardline=field_pos,
                play_type=play_type,
                concept=concept,
                yards_gained=yards,
                offense_player_id=offense_player.player_id if offense_player else None,
                target_player_id=target_player.player_id if target_player else None,
                defense_player_id=defense_player.player_id if defense_player else None,
                is_touchdown=1 if touchdown else 0,
                is_turnover=1 if turnover else 0,
                clock_elapsed_tenths=live_tenths + runoff_tenths,
                runoff_tenths=runoff_tenths,
                description=description,
            )
        )
        self.record_play_injuries(
            offense=offense,
            defense=defense,
            play_type=play_type,
            concept=concept,
            yards=yards,
            offense_player=offense_player,
            target_player=target_player,
            defense_player=defense_player,
            touchdown=touchdown,
            turnover=turnover,
        )

    def run_drive(self, offense: TeamSnapshot, start_field: int) -> tuple[TeamSnapshot, int, bool]:
        defense = self.opponent(offense)
        down = 1
        distance = 10
        field_pos = int(start_field)
        self.drive_number += 1
        drive = DriveRecord(
            drive_number=self.drive_number,
            offense_team_id=offense.team_id,
            defense_team_id=defense.team_id,
            start_quarter=self.quarter,
            start_clock_tenths=self.clock_tenths,
            start_yardline=field_pos,
        )
        start_score = self.score[offense.team_id]
        start_play_count = len(self.plays)
        start_total_clock_marker = self.total_elapsed_tenths()
        drive_yards = 0

        while True:
            ended, new_offense, new_field, new_down, new_distance = self.advance_dead_quarter_if_needed(offense, field_pos, down, distance)
            if ended:
                drive.result = "half_end" if self.quarter == 3 else "game_end"
                offense, field_pos, down, distance = new_offense, new_field, new_down, new_distance
                break
            if self.should_kneel(offense, defense, down):
                old_field = field_pos
                yards, desc, qb = self.kneel_play(offense, defense, field_pos)
                live = 18
                runoff = 420
                consumed, actual_runoff = self.consume_clock(live, runoff)
                self.record_play(
                    self.drive_number,
                    offense,
                    defense,
                    down,
                    distance,
                    old_field,
                    "run",
                    "kneel",
                    yards,
                    consumed - actual_runoff,
                    actual_runoff,
                    desc,
                    offense_player=qb,
                )
                field_pos = int(clamp(field_pos + yards, 1, 99))
                drive_yards += yards
                down += 1
                if self.clock_tenths <= 0:
                    drive.result = "game_end"
                    break
                if down > 4:
                    drive.result = "turnover_on_downs"
                    offense, field_pos = defense, int(clamp(100 - field_pos, 1, 99))
                    break
                continue
            if self.should_spike(offense, down, field_pos):
                desc, qb = self.spike_play(offense)
                consumed, runoff = self.consume_clock(10, 0)
                self.record_play(
                    self.drive_number,
                    offense,
                    defense,
                    down,
                    distance,
                    field_pos,
                    "pass",
                    "spike",
                    0,
                    consumed,
                    runoff,
                    desc,
                    offense_player=qb,
                )
                down += 1
                if down > 4:
                    drive.result = "turnover_on_downs"
                    offense, field_pos = defense, int(clamp(100 - field_pos, 1, 99))
                    break
                continue
            if down == 4:
                decision = self.fourth_down_decision(offense, defense, down, distance, field_pos)
                if decision == "field_goal":
                    outcome, next_offense, next_field, desc = self.field_goal(offense, defense, field_pos)
                    live = max(25, int(round(self.rng.gauss(42, 7))))
                    consumed, runoff = self.consume_clock(live, 0)
                    self.record_play(self.drive_number, offense, defense, down, distance, field_pos, "field_goal", outcome, 0, consumed, runoff, desc, offense_player=offense.starter("PK"), touchdown=outcome == "field_goal_return_touchdown")
                    drive.result = outcome
                    offense, field_pos = next_offense, next_field
                    break
                if decision == "punt":
                    outcome, next_offense, next_field, desc, returner = self.punt(offense, defense, field_pos)
                    live = max(30, int(round(self.rng.gauss(47, 7))))
                    consumed, runoff = self.consume_clock(live, 0)
                    self.record_play(self.drive_number, offense, defense, down, distance, field_pos, "punt", outcome, 0, consumed, runoff, desc, offense_player=offense.starter("PT"), defense_player=returner, touchdown=outcome == "punt_return_touchdown")
                    drive.result = outcome
                    offense, field_pos = next_offense, next_field
                    break

            is_pass = self.play_call_is_pass(offense, defense, down, distance, field_pos)
            play_type = "pass" if is_pass else "run"
            presnap_penalty = self.make_presnap_penalty(offense, defense, play_type)
            if presnap_penalty:
                decision = self.penalty_decision(
                    presnap_penalty,
                    snap_down=down,
                    snap_distance=distance,
                    snap_field=field_pos,
                    dead_field=field_pos,
                )
                snap_down = down
                snap_distance = distance
                snap_field = field_pos
                self.mark_penalty_accepted(presnap_penalty, offense, defense, decision.enforced_yards, decision.first_down)
                field_pos = decision.field_pos
                down = decision.down
                distance = decision.distance
                desc = self.penalty_description(presnap_penalty, self.penalty_team(presnap_penalty, offense, defense), decision)
                self.record_play(self.drive_number, offense, defense, snap_down, snap_distance, snap_field, "penalty", presnap_penalty.label, decision.enforced_yards, 0, 0, desc)
                continue

            old_field = field_pos
            self._last_play_concept = None
            live_flags = self.maybe_live_penalties(offense, defense, play_type, field_pos)
            state_snapshot = self.state_snapshot() if live_flags else None
            if is_pass:
                outcome, yards, _points, _unused, desc, next_offense, next_field, _down, _dist, target, defender = self.pass_play(offense, defense, down, distance, field_pos)
                live = int(clamp(round(self.rng.gauss(42 if yards == 0 else 58, 13)), 18, 110))
                stops_clock = yards == 0 and outcome == "normal"
                if outcome in {"touchdown", "turnover", "defensive_touchdown"}:
                    stops_clock = True
                runoff = 0 if stops_clock else int(clamp(round(self.rng.gauss(285, 55)), 90, 410))
                runoff, timeout_desc = self.maybe_use_timeout(offense, defense, play_type="pass", runoff_tenths=runoff, stops_clock=stops_clock)
                desc += timeout_desc
                consumed, actual_runoff = self.consume_clock(live, runoff)
                offense_player = self.active_starter(offense, "QB")
                target_player = target
                defense_player = defender
            else:
                outcome, yards, _points, _unused, desc, next_offense, next_field, _down, _dist, runner, tackler = self.run_play(offense, defense, down, distance, field_pos)
                live = int(clamp(round(self.rng.gauss(58, 16)), 25, 120))
                runoff = 0 if outcome in {"touchdown", "turnover", "defensive_touchdown"} else int(clamp(round(self.rng.gauss(305, 55)), 110, 420))
                stops_clock = outcome in {"touchdown", "turnover", "defensive_touchdown"}
                runoff, timeout_desc = self.maybe_use_timeout(offense, defense, play_type="run", runoff_tenths=runoff, stops_clock=stops_clock)
                desc += timeout_desc
                consumed, actual_runoff = self.consume_clock(live, runoff)
                offense_player = runner
                target_player = None
                defense_player = tackler

            play_concept = self._last_play_concept or play_type
            live_elapsed = consumed - actual_runoff
            dead_field = int(clamp(old_field + yards, 1, 99))
            if live_flags:
                if len({flag.side for flag in live_flags}) > 1:
                    if state_snapshot:
                        self.restore_state_snapshot(state_snapshot)
                    self.mark_offsetting_penalties(live_flags, offense, defense)
                    self.count_scrimmage_snap(offense, defense, play_type, play_concept)
                    labels = " and ".join(f"{flag.label} on {self.penalty_team(flag, offense, defense).abbreviation}" for flag in live_flags)
                    self.record_play(
                        self.drive_number,
                        offense,
                        defense,
                        down,
                        distance,
                        old_field,
                        "penalty",
                        "offsetting",
                        0,
                        live_elapsed,
                        actual_runoff,
                        f"Offsetting penalties: {labels}. Replay down.",
                    )
                    continue

                flag = live_flags[0]
                decision = self.penalty_decision(
                    flag,
                    snap_down=down,
                    snap_distance=distance,
                    snap_field=old_field,
                    dead_field=dead_field,
                    play_outcome=outcome,
                )
                if self.should_accept_penalty(flag, decision, snap_down=down, snap_distance=distance, play_yards=yards, outcome=outcome):
                    penalized = self.penalty_team(flag, offense, defense)
                    if not decision.keeps_play:
                        if state_snapshot:
                            self.restore_state_snapshot(state_snapshot)
                        self.mark_penalty_accepted(flag, offense, defense, decision.enforced_yards, decision.first_down)
                        self.count_scrimmage_snap(offense, defense, play_type, play_concept)
                        self.record_play(
                            self.drive_number,
                            offense,
                            defense,
                            down,
                            distance,
                            old_field,
                            "penalty",
                            flag.label,
                            decision.enforced_yards,
                            live_elapsed,
                            actual_runoff,
                            self.penalty_description(flag, penalized, decision),
                        )
                        field_pos = decision.field_pos
                        down = decision.down
                        distance = decision.distance
                        if decision.turnover_on_downs:
                            drive.result = "turnover_on_downs"
                            offense, field_pos = defense, int(clamp(100 - field_pos, 1, 99))
                            break
                        continue

                    self.mark_penalty_accepted(flag, offense, defense, decision.enforced_yards, decision.first_down)
                    desc = f"{desc} {self.penalty_description(flag, penalized, decision)}"
                    self.record_play(
                        self.drive_number,
                        offense,
                        defense,
                        down,
                        distance,
                        old_field,
                        play_type,
                        play_concept,
                        yards,
                        live_elapsed,
                        actual_runoff,
                        desc,
                        offense_player=offense_player,
                        target_player=target_player,
                        defense_player=defense_player,
                        touchdown=outcome in {"touchdown", "defensive_touchdown"},
                        turnover=outcome in {"turnover", "defensive_touchdown"},
                    )
                    drive_yards += yards
                    field_pos = decision.field_pos
                    down = decision.down
                    distance = decision.distance
                    continue

                self.mark_penalty_declined(flag, offense, defense)
                desc = f"{desc} {self.penalty_description(flag, self.penalty_team(flag, offense, defense), decision, accepted=False)}"

            self.record_play(
                self.drive_number,
                offense,
                defense,
                down,
                distance,
                old_field,
                play_type,
                play_concept,
                yards,
                live_elapsed,
                actual_runoff,
                desc,
                offense_player=offense_player,
                target_player=target_player,
                defense_player=defense_player,
                touchdown=outcome in {"touchdown", "defensive_touchdown"},
                turnover=outcome in {"turnover", "defensive_touchdown"},
            )

            if outcome in {"touchdown", "turnover", "defensive_touchdown"}:
                if outcome == "touchdown":
                    drive_yards += max(0, yards)
                drive.result = outcome
                offense, field_pos = next_offense, next_field
                break

            field_pos = int(next_field)
            drive_yards += yards
            if old_field + yards <= 0:
                self.add_score(defense, 2)
                self.team_stats[defense.team_id]["safeties"] += 1
                drive.result = "safety"
                offense, field_pos = defense, 25
                break
            if yards >= distance:
                self.team_stats[offense.team_id]["first_downs"] += 1
                down = 1
                distance = min(10, max(1, 100 - field_pos))
            else:
                down += 1
                distance = max(1, distance - yards)
                if down > 4:
                    drive.result = "turnover_on_downs"
                    offense, field_pos = defense, int(clamp(100 - field_pos, 1, 99))
                    break

        drive.end_quarter = self.quarter
        drive.end_clock_tenths = self.clock_tenths
        drive.end_yardline = field_pos
        drive.plays = len(self.plays) - start_play_count
        drive.yards = drive_yards
        drive.points = self.score[drive.offense_team_id] - start_score
        drive.time_elapsed_tenths = max(0, self.total_elapsed_tenths() - start_total_clock_marker)
        if not drive.result:
            drive.result = "end"
        self.drives.append(drive)
        game_finished = self.quarter >= 4 and self.clock_tenths == 0
        if self.quarter == 5 and self.clock_tenths == 0:
            game_finished = True
        return offense, field_pos, game_finished

    def free_kick_clock_available(self) -> bool:
        if self.clock_tenths > 0:
            return True
        if self.quarter in {1, 3}:
            self.quarter += 1
            self.clock_tenths = REGULATION_QUARTER_TENTHS
            return True
        return False

    def should_attempt_onside(self, kicking_team: TeamSnapshot) -> bool:
        if self.current_score_diff(kicking_team) >= 0:
            return False
        if self.quarter < 4:
            return False
        return self.clock_tenths <= 5 * 60 * TENTHS_PER_SECOND

    def resolve_free_kick(
        self,
        kicking_team: TeamSnapshot,
        receiving_team: TeamSnapshot,
        *,
        reason: str,
        safety_kick: bool = False,
        allow_onside: bool = True,
    ) -> tuple[TeamSnapshot, int]:
        attempts = 0
        while attempts < 4:
            attempts += 1
            if reason not in {"opening", "second_half", "overtime"} and not self.free_kick_clock_available():
                return receiving_team, 25
            onside = allow_onside and not safety_kick and self.should_attempt_onside(kicking_team)
            outcome, next_offense, next_field = self.kickoff(
                kicking_team,
                receiving_team,
                reason=reason,
                safety_kick=safety_kick,
                onside=onside,
            )
            if outcome != "touchdown":
                return next_offense, next_field
            kicking_team, receiving_team = receiving_team, kicking_team
            reason = "post_score"
            safety_kick = False
            allow_onside = True
        return receiving_team, 25

    def possession_after_drive(self, drive: DriveRecord, fallback_offense: TeamSnapshot, fallback_field: int) -> tuple[TeamSnapshot, int, bool]:
        if drive.result == "half_end":
            self.reset_half_timeouts()
            receiver = self.second_half_receiver
            return (*self.resolve_free_kick(self.opponent(receiver), receiver, reason="second_half"), False)
        if drive.result in {"touchdown", "field_goal"}:
            scoring = self.team_by_id(drive.offense_team_id)
            receiving = self.team_by_id(drive.defense_team_id)
            return (*self.resolve_free_kick(scoring, receiving, reason="post_score"), False)
        if drive.result in {"defensive_touchdown", "field_goal_return_touchdown", "punt_return_touchdown"}:
            scoring = self.team_by_id(drive.defense_team_id)
            receiving = self.team_by_id(drive.offense_team_id)
            return (*self.resolve_free_kick(scoring, receiving, reason="post_score"), False)
        if drive.result == "safety":
            kicking = self.team_by_id(drive.offense_team_id)
            receiving = self.team_by_id(drive.defense_team_id)
            return (*self.resolve_free_kick(kicking, receiving, reason="safety_kick", safety_kick=True, allow_onside=False), False)
        return fallback_offense, fallback_field, False

    def total_elapsed_tenths(self) -> int:
        if self.quarter <= 4:
            return (self.quarter - 1) * REGULATION_QUARTER_TENTHS + (REGULATION_QUARTER_TENTHS - self.clock_tenths)
        return 4 * REGULATION_QUARTER_TENTHS + (OVERTIME_TENTHS - self.clock_tenths)

    def overtime_should_continue(self) -> bool:
        away_score = self.score[self.away.team_id]
        home_score = self.score[self.home.team_id]
        if self.clock_tenths <= 0:
            return False
        if away_score == home_score:
            return True
        if len(self.ot_possessions) < 2:
            return True
        return False

    def simulate(self) -> GameResult:
        offense, field_pos = self.resolve_free_kick(
            self.opponent(self.first_half_receiver),
            self.first_half_receiver,
            reason="opening",
            allow_onside=False,
        )
        finished = False
        while not finished:
            offense, field_pos, finished = self.run_drive(offense, field_pos)
            if self.drives:
                last_drive = self.drives[-1]
                if not finished:
                    offense, field_pos, _ = self.possession_after_drive(last_drive, offense, field_pos)
            if self.quarter >= 4 and self.clock_tenths == 0:
                finished = True

        if self.score[self.away.team_id] == self.score[self.home.team_id]:
            self.quarter = 5
            self.clock_tenths = OVERTIME_TENTHS
            self.reset_overtime_timeouts()
            self.ot_possessions.clear()
            receiver = self.rng.choice([self.away, self.home])
            offense, field_pos = self.resolve_free_kick(self.opponent(receiver), receiver, reason="overtime", allow_onside=False)
            while self.overtime_should_continue():
                current_offense_id = offense.team_id
                offense, field_pos, finished = self.run_drive(offense, field_pos)
                self.ot_possessions.add(current_offense_id)
                last_drive = self.drives[-1] if self.drives else None
                if last_drive and last_drive.result in {"defensive_touchdown", "field_goal_return_touchdown", "punt_return_touchdown", "safety"} and len(self.ot_possessions) == 1:
                    break
                if self.clock_tenths <= 0:
                    break
                if len(self.ot_possessions) >= 2 and self.score[self.away.team_id] != self.score[self.home.team_id]:
                    break
                if last_drive:
                    offense, field_pos, _ = self.possession_after_drive(last_drive, offense, field_pos)

        return GameResult(
            schedule_game_id=self.schedule_game_id,
            season=self.season,
            week=self.week,
            away=self.away,
            home=self.home,
            away_score=int(self.score[self.away.team_id]),
            home_score=int(self.score[self.home.team_id]),
            seed=self.seed,
            plays=self.plays,
            drives=self.drives,
            team_stats=self.team_stats,
            player_stats=self.player_stats,
            injury_events=self.injury_events,
        )


def simulate_game(
    con: sqlite3.Connection,
    *,
    away_team_id: int,
    home_team_id: int,
    season: int = DEFAULT_SEASON,
    week: int | None = None,
    schedule_game_id: int | None = None,
    seed: int | None = None,
) -> GameResult:
    game_date = injury_model.game_date_for_schedule(con, schedule_game_id, season)
    injury_model.resolve_available_injuries(con, game_date)
    away = load_team(con, away_team_id, season, as_of_date=game_date)
    home = load_team(con, home_team_id, season, as_of_date=game_date)
    return MatchEngine(
        away=away,
        home=home,
        season=season,
        week=week,
        schedule_game_id=schedule_game_id,
        seed=seed,
    ).simulate()


def schedule_game_type(con: sqlite3.Connection, schedule_game_id: int | None) -> str | None:
    if schedule_game_id is None:
        return None
    row = con.execute(
        "SELECT game_type FROM season_games WHERE game_id = ?",
        (schedule_game_id,),
    ).fetchone()
    if not row:
        return None
    return row["game_type"] if isinstance(row, sqlite3.Row) else row[0]


def result_count_flags(con: sqlite3.Connection, result: GameResult, update_schedule: bool) -> tuple[int, int]:
    game_type = schedule_game_type(con, result.schedule_game_id)
    counts = 1 if update_schedule and result.schedule_game_id is not None and game_type == "REG" else 0
    return counts, counts


def supersede_existing_schedule_runs(
    con: sqlite3.Connection,
    *,
    schedule_game_id: int,
    superseded_by_run_id: int | None = None,
) -> None:
    if superseded_by_run_id is None:
        con.execute(
            """
            UPDATE game_sim_runs
            SET counts_for_stats = 0,
                counts_for_standings = 0,
                status = 'superseded'
            WHERE schedule_game_id = ?
              AND status <> 'superseded'
            """,
            (schedule_game_id,),
        )
    else:
        con.execute(
            """
            UPDATE game_sim_runs
            SET counts_for_stats = 0,
                counts_for_standings = 0,
                status = 'superseded',
                superseded_by_run_id = ?
            WHERE schedule_game_id = ?
              AND run_id <> ?
              AND status <> 'superseded'
            """,
            (superseded_by_run_id, schedule_game_id, superseded_by_run_id),
        )


def rebuild_season_records(con: sqlite3.Connection, season: int) -> None:
    ensure_schema(con)
    con.execute("DELETE FROM season_team_records WHERE season = ?", (season,))
    con.executemany(
        """
        INSERT INTO season_team_records (season, team_id)
        VALUES (?, ?)
        """,
        [(season, int(row["team_id"])) for row in con.execute("SELECT team_id FROM teams").fetchall()],
    )
    team_rows = con.execute("SELECT team_id, conference, division FROM teams").fetchall()
    teams = {
        int(row["team_id"]): {
            "conference": row["conference"],
            "division": row["division"],
        }
        for row in team_rows
    }
    runs = con.execute(
        """
        SELECT r.*
        FROM game_sim_runs r
        JOIN season_games sg ON sg.game_id = r.schedule_game_id
        WHERE r.season = ?
          AND r.counts_for_standings = 1
          AND r.status = 'final'
          AND sg.game_type = 'REG'
        ORDER BY sg.week, sg.week_game_number, r.run_id
        """,
        (season,),
    ).fetchall()

    def apply(team_id: int, opponent_id: int, points_for: int, points_against: int) -> None:
        win = 1 if points_for > points_against else 0
        loss = 1 if points_for < points_against else 0
        tie = 1 if points_for == points_against else 0
        team = teams[team_id]
        opponent = teams[opponent_id]
        conference_game = team["conference"] == opponent["conference"]
        division_game = team["division"] == opponent["division"]
        con.execute(
            """
            UPDATE season_team_records
            SET wins = wins + ?,
                losses = losses + ?,
                ties = ties + ?,
                points_for = points_for + ?,
                points_against = points_against + ?,
                conference_wins = conference_wins + ?,
                conference_losses = conference_losses + ?,
                conference_ties = conference_ties + ?,
                division_wins = division_wins + ?,
                division_losses = division_losses + ?,
                division_ties = division_ties + ?,
                updated_at = datetime('now')
            WHERE season = ? AND team_id = ?
            """,
            (
                win,
                loss,
                tie,
                points_for,
                points_against,
                win if conference_game else 0,
                loss if conference_game else 0,
                tie if conference_game else 0,
                win if division_game else 0,
                loss if division_game else 0,
                tie if division_game else 0,
                season,
                team_id,
            ),
        )

    for run in runs:
        apply(int(run["away_team_id"]), int(run["home_team_id"]), int(run["away_score"]), int(run["home_score"]))
        apply(int(run["home_team_id"]), int(run["away_team_id"]), int(run["home_score"]), int(run["away_score"]))


def rebuild_season_stat_tables(con: sqlite3.Connection, season: int) -> None:
    ensure_schema(con)
    con.execute("DELETE FROM season_team_stats WHERE season = ?", (season,))
    con.execute("DELETE FROM season_player_stats WHERE season = ?", (season,))
    con.execute(
        """
        INSERT INTO season_team_stats (season, team_id, stat_key, stat_value, updated_at)
        SELECT r.season, gts.team_id, gts.stat_key, SUM(gts.stat_value), datetime('now')
        FROM game_team_stats gts
        JOIN game_sim_runs r ON r.run_id = gts.run_id
        JOIN season_games sg ON sg.game_id = r.schedule_game_id
        WHERE r.season = ?
          AND r.counts_for_stats = 1
          AND r.status = 'final'
          AND sg.game_type = 'REG'
        GROUP BY r.season, gts.team_id, gts.stat_key
        """,
        (season,),
    )
    con.execute(
        """
        INSERT INTO season_player_stats (season, player_id, team_id, stat_key, stat_value, updated_at)
        SELECT
            r.season,
            gps.player_id,
            gps.team_id,
            gps.stat_key,
            CASE
                WHEN gps.stat_key = 'long_fg' THEN MAX(gps.stat_value)
                ELSE SUM(gps.stat_value)
            END,
            datetime('now')
        FROM game_player_stats gps
        JOIN game_sim_runs r ON r.run_id = gps.run_id
        JOIN season_games sg ON sg.game_id = r.schedule_game_id
        WHERE r.season = ?
          AND r.counts_for_stats = 1
          AND r.status = 'final'
          AND sg.game_type = 'REG'
        GROUP BY r.season, gps.player_id, gps.team_id, gps.stat_key
        """,
        (season,),
    )


def rebuild_season_history(con: sqlite3.Connection, season: int) -> None:
    rebuild_season_records(con, season)
    rebuild_season_stat_tables(con, season)


def persist_result(
    con: sqlite3.Connection,
    result: GameResult,
    *,
    update_schedule: bool = True,
    force: bool = False,
    notes: str | None = None,
    rebuild_history: bool = True,
) -> int:
    ensure_schema(con)
    if update_schedule and result.schedule_game_id is not None and not force:
        row = con.execute(
            "SELECT played FROM season_games WHERE game_id = ?",
            (result.schedule_game_id,),
        ).fetchone()
        if row and int(row["played"] or 0):
            raise ValueError(f"Schedule game {result.schedule_game_id} is already played. Use force to overwrite.")
    if update_schedule and result.schedule_game_id is not None and force:
        injury_model.retract_schedule_game_injuries(con, result.schedule_game_id)
        supersede_existing_schedule_runs(con, schedule_game_id=result.schedule_game_id)

    counts_for_stats, counts_for_standings = result_count_flags(con, result, update_schedule)

    cur = con.execute(
        """
        INSERT INTO game_sim_runs (
            schedule_game_id, season, week, away_team_id, home_team_id, seed,
            engine_version, status, away_score, home_score, total_plays, total_drives,
            counts_for_stats, counts_for_standings, notes
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            result.schedule_game_id,
            result.season,
            result.week,
            result.away.team_id,
            result.home.team_id,
            result.seed,
            ENGINE_VERSION,
            result.status,
            result.away_score,
            result.home_score,
            len(result.plays),
            len(result.drives),
            counts_for_stats,
            counts_for_standings,
            notes,
        ),
    )
    run_id = int(cur.lastrowid)
    if update_schedule and result.schedule_game_id is not None and force:
        supersede_existing_schedule_runs(
            con,
            schedule_game_id=result.schedule_game_id,
            superseded_by_run_id=run_id,
        )

    con.executemany(
        """
        INSERT INTO game_sim_drives (
            run_id, drive_number, offense_team_id, defense_team_id,
            start_quarter, start_clock_tenths, end_quarter, end_clock_tenths,
            start_yardline, end_yardline, result, plays, yards, points, time_elapsed_tenths
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                run_id,
                drive.drive_number,
                drive.offense_team_id,
                drive.defense_team_id,
                drive.start_quarter,
                drive.start_clock_tenths,
                drive.end_quarter,
                drive.end_clock_tenths,
                drive.start_yardline,
                drive.end_yardline,
                drive.result,
                drive.plays,
                drive.yards,
                drive.points,
                drive.time_elapsed_tenths,
            )
            for drive in result.drives
        ],
    )

    con.executemany(
        """
        INSERT INTO game_sim_plays (
            run_id, play_number, drive_number, quarter, clock_tenths,
            offense_team_id, defense_team_id, down, distance, yardline,
            play_type, concept, offense_player_id, target_player_id,
            defense_player_id, yards_gained, is_touchdown, is_turnover,
            clock_elapsed_tenths, runoff_tenths, description
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                run_id,
                play.play_number,
                play.drive_number,
                play.quarter,
                play.clock_tenths,
                play.offense_team_id,
                play.defense_team_id,
                play.down,
                play.distance,
                play.yardline,
                play.play_type,
                play.concept,
                play.offense_player_id,
                play.target_player_id,
                play.defense_player_id,
                play.yards_gained,
                play.is_touchdown,
                play.is_turnover,
                play.clock_elapsed_tenths,
                play.runoff_tenths,
                play.description,
            )
            for play in result.plays
        ],
    )

    for team_id, stats in result.team_stats.items():
        con.executemany(
            """
            INSERT INTO game_team_stats (run_id, team_id, stat_key, stat_value)
            VALUES (?, ?, ?, ?)
            """,
            [(run_id, team_id, key, float(value)) for key, value in stats.items()],
        )

    player_team = {p.player_id: result.away.team_id for p in result.away.roster}
    player_team.update({p.player_id: result.home.team_id for p in result.home.roster})
    for player_id, stats in result.player_stats.items():
        team_id = player_team.get(player_id)
        if team_id is None:
            continue
        con.executemany(
            """
            INSERT INTO game_player_stats (run_id, player_id, team_id, stat_key, stat_value)
            VALUES (?, ?, ?, ?, ?)
            """,
            [(run_id, player_id, team_id, key, float(value)) for key, value in stats.items()],
        )

    injury_model.persist_game_injuries(con, result, run_id)

    if update_schedule and result.schedule_game_id is not None:
        con.execute(
            """
            UPDATE season_games
            SET played = 1,
                away_score = ?,
                home_score = ?,
                updated_at = datetime('now')
            WHERE game_id = ?
            """,
            (result.away_score, result.home_score, result.schedule_game_id),
        )
        if rebuild_history:
            rebuild_season_history(con, result.season)

    return run_id


def update_records(con: sqlite3.Connection, result: GameResult) -> None:
    teams = [result.away, result.home]
    for team in teams:
        con.execute(
            """
            INSERT INTO season_team_records (season, team_id)
            VALUES (?, ?)
            ON CONFLICT(season, team_id) DO NOTHING
            """,
            (result.season, team.team_id),
        )

    away_outcome = "tie"
    home_outcome = "tie"
    if result.away_score > result.home_score:
        away_outcome, home_outcome = "win", "loss"
    elif result.home_score > result.away_score:
        away_outcome, home_outcome = "loss", "win"

    def apply(team: TeamSnapshot, opponent: TeamSnapshot, points_for: int, points_against: int, outcome: str) -> None:
        win = 1 if outcome == "win" else 0
        loss = 1 if outcome == "loss" else 0
        tie = 1 if outcome == "tie" else 0
        conf = team.conference == opponent.conference
        div = team.division == opponent.division
        con.execute(
            """
            UPDATE season_team_records
            SET wins = wins + ?,
                losses = losses + ?,
                ties = ties + ?,
                points_for = points_for + ?,
                points_against = points_against + ?,
                conference_wins = conference_wins + ?,
                conference_losses = conference_losses + ?,
                conference_ties = conference_ties + ?,
                division_wins = division_wins + ?,
                division_losses = division_losses + ?,
                division_ties = division_ties + ?,
                updated_at = datetime('now')
            WHERE season = ? AND team_id = ?
            """,
            (
                win,
                loss,
                tie,
                points_for,
                points_against,
                win if conf else 0,
                loss if conf else 0,
                tie if conf else 0,
                win if div else 0,
                loss if div else 0,
                tie if div else 0,
                result.season,
                team.team_id,
            ),
        )

    apply(result.away, result.home, result.away_score, result.home_score, away_outcome)
    apply(result.home, result.away, result.home_score, result.away_score, home_outcome)


def scoreline(result: GameResult) -> str:
    return (
        f"{result.away.abbreviation} {result.away_score} at "
        f"{result.home.abbreviation} {result.home_score}"
    )
