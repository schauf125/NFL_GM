"""Prototype 0.1-second tick resolver for football plays.

This module is intentionally stateless. It reads the same TeamSnapshot and
PlayerSnapshot objects used by match_engine, resolves one pass play in small
time slices, and returns a structured result plus a debug log. The main match
engine can keep using its stable per-play model until the tick resolver is
ready to be wired behind a feature flag.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any

from engine.match_engine import (
    COVERAGE_WEIGHTS,
    PASS_BLOCK_WEIGHTS,
    PASS_RUSH_WEIGHTS,
    QB_PASS_WEIGHTS,
    QB_SCRAMBLE_WEIGHTS,
    RECEIVER_WEIGHTS,
    TACKLE_WEIGHTS,
    YAC_WEIGHTS,
    PlayerSnapshot,
    TeamSnapshot,
    average,
    clamp,
    weighted_average,
    weighted_choice,
    sack_credit_weight,
)
from engine.qb_behavior import QBBehaviorProfile, qb_behavior_profile


PASS_CONCEPTS = ("screen", "quick", "short", "intermediate", "deep")


@dataclass(frozen=True)
class TickConfig:
    tick_seconds: float = 0.1
    max_ticks: int = 60
    decision_tick_floor: int = 7
    hot_throw_after_pressure_ticks: int = 2
    throw_after_pressure_ticks: int = 4
    sack_after_pressure_ticks: int = 7
    scramble_after_pressure_ticks: int = 5
    open_threshold: float = 1.15
    debug_ticks: bool = False


@dataclass
class TickEvent:
    tick: int
    time_seconds: float
    kind: str
    description: str
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class RouteTickState:
    receiver: PlayerSnapshot
    defender: PlayerSnapshot
    depth: int
    break_tick: int
    target_priority: float
    route_slot: str = ""
    read_rank: int = 0
    route_role: str = "secondary"
    separation: float = 0.0
    clean_release: bool = False
    open_tick: int | None = None
    final_open_score: float = 0.0


@dataclass
class TickPassResult:
    concept: str
    outcome: str
    yards: int
    air_yards: int
    yac_yards: int
    ticks_elapsed: int
    time_elapsed_seconds: float
    quarterback: PlayerSnapshot
    qb_profile: QBBehaviorProfile
    target: PlayerSnapshot | None
    defender: PlayerSnapshot | None
    rusher: PlayerSnapshot | None
    throw_tick: int | None
    pressure_tick: int | None
    sack_tick: int | None
    completion_probability: float
    interception_probability: float
    pressure_score: float
    best_open_score: float
    description: str
    routes: list[RouteTickState]
    events: list[TickEvent]


class TickPassResolver:
    def __init__(self, *, rng: random.Random | None = None, config: TickConfig | None = None) -> None:
        self.rng = rng or random.Random()
        self.config = config or TickConfig()

    def choose_concept(self, down: int, distance: int, field_pos: int, profile: QBBehaviorProfile | None = None) -> str:
        red_zone = field_pos >= 80
        screen = 0.55 if distance >= 7 else 0.35
        quick = 1.35 if distance <= 4 or red_zone else 0.90
        short = 1.15
        intermediate = 1.00 if distance >= 5 else 0.55
        deep = 0.32 if red_zone else 0.45 if down < 3 else 0.70
        if profile:
            screen += (profile.checkdown_willingness - 50) * 0.010
            quick += (profile.rhythm - 50) * 0.010 + (profile.checkdown_willingness - 50) * 0.004
            short += (profile.rhythm - 50) * 0.006
            intermediate += (profile.deep_aggression - 50) * 0.004
            deep += (profile.deep_aggression - 50) * 0.012 + (profile.broken_play_creation - 50) * 0.004
        return weighted_choice(
            self.rng,
            [
                ("screen", screen),
                ("quick", quick),
                ("short", short),
                ("intermediate", intermediate),
                ("deep", deep),
            ],
        )

    def select_rusher(self, defense: TeamSnapshot) -> PlayerSnapshot:
        rushers = defense.defensive_front() or defense.roster[:8]
        return weighted_choice(self.rng, [(player, sack_credit_weight(player)) for player in rushers])

    def route_assignments(self, offense: TeamSnapshot) -> list[tuple[str, PlayerSnapshot]]:
        assignments: list[tuple[str, PlayerSnapshot]] = []
        used: set[int] = set()
        for slot in ("LWR", "RWR", "SWR", "TE", "RB"):
            for player in offense.candidates(slot):
                if player.player_id in used:
                    continue
                assignments.append((slot, player))
                used.add(player.player_id)
                break
        if assignments:
            return assignments
        return [(f"REC{idx + 1}", player) for idx, player in enumerate(offense.receiving_options())]

    def defender_slot_order(self, route_slot: str, receiver: PlayerSnapshot, concept: str, depth: int) -> list[list[str]]:
        if route_slot == "LWR":
            return [["LCB"], ["RCB"], ["NB"], ["FS", "SS"], ["MLB", "WLB", "SLB"]]
        if route_slot == "RWR":
            return [["RCB"], ["LCB"], ["NB"], ["FS", "SS"], ["MLB", "WLB", "SLB"]]
        if route_slot == "SWR":
            return [["NB"], ["LCB", "RCB"], ["SS"], ["WLB", "MLB"], ["FS"]]
        if receiver.position == "TE":
            if concept == "deep" or depth >= 10:
                return [["SS"], ["FS"], ["NB"], ["MLB", "WLB", "SLB"], ["LCB", "RCB"]]
            return [["SS"], ["MLB", "WLB", "SLB"], ["NB"], ["FS"], ["LCB", "RCB"]]
        if receiver.position == "RB":
            return [["WLB"], ["MLB"], ["SS"], ["NB"], ["FS"], ["LCB", "RCB"]]
        return [["LCB", "RCB", "NB"], ["FS", "SS"], ["MLB", "WLB", "SLB"]]

    def select_coverage_defender(
        self,
        defense: TeamSnapshot,
        *,
        route_slot: str,
        receiver: PlayerSnapshot,
        concept: str,
        depth: int,
        used: set[int],
    ) -> PlayerSnapshot:
        selected_pool: list[PlayerSnapshot] = []
        for slot_group in self.defender_slot_order(route_slot, receiver, concept, depth):
            pool: list[PlayerSnapshot] = []
            for slot in slot_group:
                available = [player for player in defense.candidates(slot) if player.player_id not in used]
                if available:
                    pool.append(available[0])
            if pool:
                selected_pool = pool
                break
        if not selected_pool:
            selected_pool = [player for player in defense.secondary() + defense.linebackers() if player.player_id not in used]
        if not selected_pool:
            selected_pool = defense.secondary() + defense.linebackers() or defense.roster[:11]

        def matchup_score(player: PlayerSnapshot) -> float:
            score = weighted_average(player, COVERAGE_WEIGHTS)
            if receiver.position == "WR" and player.position == "CB":
                score += 7.5
            if receiver.position == "TE" and player.position in {"S", "FS", "SS", "LB", "ILB", "OLB"}:
                score += 4.0
            if receiver.position == "RB" and player.position in {"LB", "ILB", "OLB", "S", "FS", "SS"}:
                score += 5.5
            if depth >= 14 and player.position in {"S", "FS", "SS"}:
                score += 2.5
            return score

        defender = weighted_choice(self.rng, [(player, matchup_score(player)) for player in selected_pool])
        used.add(defender.player_id)
        return defender

    def route_depths(self, concept: str, field_pos: int, distance: int) -> list[int]:
        if concept == "screen":
            depths = [-1, 3, 5, 4, 0]
        elif concept == "quick":
            depths = [3, 5, 6, 4, 1]
        elif concept == "short":
            depths = [6, 8, 7, 5, 1]
        elif concept == "intermediate":
            depths = [11, 14, 10, 8, 2]
        else:
            depths = [22, 18, 14, 10, 2]
        capped = []
        for depth in depths:
            cap = max(1, 100 - field_pos)
            if distance <= 3 and concept != "deep":
                cap = min(cap, 8)
            capped.append(int(clamp(depth, -2, cap)))
        return capped

    def read_order(self, concept: str) -> list[str]:
        return {
            "screen": ["RB", "TE", "SWR", "LWR", "RWR"],
            "quick": ["SWR", "LWR", "RWR", "TE", "RB"],
            "short": ["LWR", "SWR", "TE", "RWR", "RB"],
            "intermediate": ["LWR", "RWR", "TE", "SWR", "RB"],
            "deep": ["LWR", "RWR", "SWR", "TE", "RB"],
        }[concept]

    def route_role(self, concept: str, route_slot: str, depth: int, read_rank: int) -> str:
        if concept == "screen":
            if route_slot == "RB":
                return "primary"
            if depth <= 5:
                return "outlet"
            return "clearout"
        if read_rank == 0:
            return "primary"
        if depth <= 4 or route_slot == "RB":
            return "outlet"
        if concept == "deep" and depth >= 12:
            return "primary" if read_rank <= 1 else "secondary"
        if concept == "intermediate" and 8 <= depth <= 16:
            return "secondary"
        if concept in {"quick", "short"} and 3 <= depth <= 10:
            return "secondary"
        if read_rank <= 2:
            return "secondary"
        return "outlet"

    def concept_primary_depth_fit(self, concept: str, depth: int, route_slot: str) -> bool:
        if concept == "screen":
            return route_slot == "RB"
        if concept == "quick":
            return 1 <= depth <= 7
        if concept == "short":
            return 4 <= depth <= 10
        if concept == "intermediate":
            return 8 <= depth <= 16
        return depth >= 12

    def concept_depth_bonus(self, concept: str, depth: int, route_slot: str) -> float:
        if concept == "screen":
            return 18.0 if route_slot == "RB" else -2.0 if depth <= 5 else -6.0
        if concept == "quick":
            return 7.0 if 1 <= depth <= 7 else -3.0
        if concept == "short":
            return 7.0 if 4 <= depth <= 10 else -2.0 if depth <= 3 else 1.0
        if concept == "intermediate":
            return 8.0 if 8 <= depth <= 16 else -4.0 if depth <= 3 else 2.0
        return 10.0 if depth >= 18 else 6.0 if depth >= 12 else -7.0 if depth <= 4 else 1.0

    def build_routes(self, offense: TeamSnapshot, defense: TeamSnapshot, concept: str, field_pos: int, distance: int) -> list[RouteTickState]:
        assignments = self.route_assignments(offense)
        depths = self.route_depths(concept, field_pos, distance)
        read_order = self.read_order(concept)
        used_defenders: set[int] = set()
        receiver_scores = [weighted_average(player, RECEIVER_WEIGHTS) for _slot, player in assignments]
        receiver_score_avg = average(receiver_scores)
        top_receiver_score = max(receiver_scores, default=receiver_score_avg)
        routes = []
        for idx, (route_slot, receiver) in enumerate(assignments):
            depth = depths[idx % len(depths)]
            defender = self.select_coverage_defender(
                defense,
                route_slot=route_slot,
                receiver=receiver,
                concept=concept,
                depth=depth,
                used=used_defenders,
            )
            release = receiver.rating("release_vs_press")
            route_snap = receiver.rating("route_snap")
            route_timing = receiver.rating("route_timing")
            receiver_score = receiver_scores[idx]
            read_rank = read_order.index(route_slot) if route_slot in read_order else idx
            if concept != "screen" and receiver.position in {"WR", "TE"} and receiver_score >= top_receiver_score - 1.5:
                read_rank = min(read_rank, 0 if self.concept_primary_depth_fit(concept, depth, route_slot) else 1)
            elif concept != "screen" and receiver_score >= receiver_score_avg + 5.0:
                read_rank = max(0, read_rank - 2)
            break_tick = {
                "screen": 5,
                "quick": 8,
                "short": 12,
                "intermediate": 17,
                "deep": 22,
            }[concept]
            break_tick += int(round(max(0, depth) * 0.18))
            break_tick -= int(round((route_snap - 65) * 0.035))
            break_tick = int(clamp(break_tick, 4, self.config.max_ticks - 5))
            target_priority = receiver_score + (route_timing - 65) * 0.18 + (release - 65) * 0.10
            target_priority += max(0.0, receiver_score - receiver_score_avg) * 0.32
            target_priority += self.concept_depth_bonus(concept, depth, route_slot)
            target_priority += max(0.0, 5.0 - read_rank) * 1.7
            if route_slot == "LWR":
                target_priority += 2.5
            routes.append(
                RouteTickState(
                    receiver=receiver,
                    defender=defender,
                    depth=depth,
                    break_tick=break_tick,
                    target_priority=target_priority,
                    route_slot=route_slot,
                    read_rank=read_rank,
                    route_role=self.route_role(concept, route_slot, depth, read_rank),
                )
            )
        return routes

    def pressure_arrival_tick(
        self,
        offense: TeamSnapshot,
        defense: TeamSnapshot,
        concept: str,
        qb: PlayerSnapshot,
        profile: QBBehaviorProfile,
    ) -> tuple[int | None, float]:
        pass_block = offense.pass_block_score()
        pass_rush = defense.pass_rush_score()
        qb_processing = average([qb.rating("processing_speed"), qb.rating("play_recognition"), qb.rating("throw_release")])
        style_pressure = (
            (profile.pocket_drift - 50) * 0.045
            + (profile.sack_risk - 50) * 0.035
            - (profile.pocket_discipline - 50) * 0.025
        )
        pressure_score = pass_rush - pass_block - (qb_processing - 65) * 0.20 + style_pressure
        base = {
            "screen": 25,
            "quick": 24,
            "short": 27,
            "intermediate": 30,
            "deep": 33,
        }[concept]
        mean_tick = base - pressure_score * 0.18 - (profile.pocket_drift - 50) * 0.025 + (profile.rhythm - 50) * 0.025
        arrival = int(round(self.rng.gauss(mean_tick, 5.0)))
        chance = clamp(
            0.54
            + pressure_score * 0.010
            + (profile.pocket_drift - 50) * 0.0020
            + (profile.sack_risk - 50) * 0.0012
            - (profile.throwaway_discipline - 50) * 0.0010,
            0.16,
            0.88,
        )
        if self.rng.random() > chance:
            return None, pressure_score
        return int(clamp(arrival, 8, self.config.max_ticks - 2)), pressure_score

    def update_route(self, route: RouteTickState, tick: int) -> None:
        receiver = route.receiver
        defender = route.defender
        receiver_score = weighted_average(receiver, RECEIVER_WEIGHTS)
        coverage_score = weighted_average(defender, COVERAGE_WEIGHTS)
        release_advantage = receiver.rating("release_vs_press") - defender.rating("press_coverage")
        route_advantage = receiver_score - coverage_score
        if tick <= 6:
            route.separation = clamp(0.72 + release_advantage * 0.010 + self.rng.gauss(0, 0.05), 0.15, 2.2)
            route.clean_release = route.separation >= 0.88
        elif tick < route.break_tick:
            growth = (tick - 6) * (0.020 + max(0, route_advantage) * 0.0007)
            route.separation = clamp(route.separation + growth + self.rng.gauss(0, 0.035), 0.10, 3.8)
        else:
            snap_bonus = (receiver.rating("route_snap") - defender.rating("agility")) * 0.012
            recognition_drag = (defender.rating("play_recognition") - receiver.rating("route_timing")) * 0.004
            route.separation = clamp(route.separation + snap_bonus - recognition_drag + self.rng.gauss(0, 0.055), 0.10, 4.6)
        route.final_open_score = route.separation + (route.target_priority - 65) * 0.010
        if route.open_tick is None and tick >= route.break_tick - 3 and route.final_open_score >= self.config.open_threshold:
            route.open_tick = tick

    def qb_decision_interval(self, qb: PlayerSnapshot, profile: QBBehaviorProfile | None = None) -> int:
        processing = average([qb.rating("processing_speed"), qb.rating("play_recognition"), qb.rating("composure")])
        interval = round(5 - (processing - 50) / 18)
        if profile:
            if profile.pocket_drift >= 75 and profile.rhythm < 68:
                interval += 1
            if profile.deep_aggression >= 82 and profile.checkdown_willingness < 42:
                interval += 1
            if profile.rhythm >= 82 and profile.pocket_discipline >= 70:
                interval -= 1
            if profile.checkdown_willingness >= 74:
                interval -= 1
        return int(clamp(interval, 2, 6))

    def route_is_readable(
        self,
        route: RouteTickState,
        tick: int,
        pressured: bool,
        concept: str,
        pressure_elapsed: int,
        profile: QBBehaviorProfile,
    ) -> bool:
        ready_tick = max(self.config.decision_tick_floor, route.break_tick - 3)
        if pressured and pressure_elapsed >= self.config.hot_throw_after_pressure_ticks:
            if route.depth <= 6 or route.receiver.position in {"RB", "TE"} or route.route_role == "outlet":
                checkdown_bonus = 2 if profile.checkdown_willingness >= 68 else 0
                checkdown_drag = 2 if profile.checkdown_willingness <= 40 and profile.broken_play_creation >= 70 else 0
                return tick >= ready_tick - 1 - checkdown_bonus + checkdown_drag
            creator_bonus = 1 if profile.broken_play_creation >= 75 and profile.deep_aggression >= 65 else 0
            return tick >= ready_tick + max(0, route.read_rank - 1) - creator_bonus
        read_delay = route.read_rank * 2
        if route.route_role == "outlet":
            read_delay += 5 if concept in {"intermediate", "deep"} else 2
            if profile.checkdown_willingness >= 70:
                read_delay -= 2
            elif profile.checkdown_willingness <= 40:
                read_delay += 2
        if route.route_role == "clearout":
            read_delay += 7
        if route.depth >= 12 and profile.deep_aggression >= 78:
            read_delay -= 1
        if route.route_role == "primary" and profile.pocket_drift >= 82 and profile.rhythm < 60:
            read_delay += 1
        return tick >= ready_tick + read_delay

    def concept_fit(self, route: RouteTickState, concept: str, pressured: bool, profile: QBBehaviorProfile) -> float:
        if pressured and (route.depth <= 6 or route.route_role == "outlet"):
            return 1.22 + (profile.checkdown_willingness - 50) * 0.004
        if concept == "screen":
            return 1.42 if route.route_slot == "RB" else 0.70 if route.route_role == "clearout" else 0.95
        if concept == "quick":
            return 1.20 if 1 <= route.depth <= 7 else 0.78
        if concept == "short":
            return 1.18 if 4 <= route.depth <= 10 else 0.86 if route.depth <= 3 else 1.00
        if concept == "intermediate":
            return 1.24 if 8 <= route.depth <= 16 else 0.68 if route.depth <= 3 else 1.00
        return 1.32 if route.depth >= 18 else 1.12 if route.depth >= 12 else 0.52 if route.depth <= 4 else 0.90

    def choose_throw_target(
        self,
        routes: list[RouteTickState],
        tick: int,
        pressured: bool,
        concept: str,
        profile: QBBehaviorProfile,
        pressure_elapsed: int = 0,
    ) -> RouteTickState | None:
        candidates = [
            route
            for route in routes
            if self.route_is_readable(route, tick, pressured, concept, pressure_elapsed, profile)
        ]
        if not candidates:
            return None
        if pressured and pressure_elapsed >= self.config.hot_throw_after_pressure_ticks:
            hot_routes = [
                route
                for route in candidates
                if route.depth <= 6 or route.receiver.position in {"RB", "TE"} or route.route_role == "outlet"
            ]
            if hot_routes and (profile.checkdown_willingness >= 55 or pressure_elapsed >= self.config.throw_after_pressure_ticks + 2):
                candidates = hot_routes
        elif concept == "deep" and tick < 29:
            candidates = [route for route in candidates if route.depth >= 10 and route.route_role != "outlet"]
        elif concept == "intermediate" and tick < 23:
            candidates = [route for route in candidates if route.depth >= 5 and route.route_role != "outlet"]
        if not candidates:
            return None
        threshold = self.config.open_threshold - (0.32 if pressured else 0.0)
        threshold += max(0.0, 58 - profile.rhythm) * 0.003
        open_routes = [route for route in candidates if route.final_open_score >= threshold]
        if not open_routes:
            return None
        hold_chance = clamp(
            (profile.pocket_drift - 70) * 0.008
            + (profile.broken_play_creation - 75) * 0.004
            + max(0.0, 50 - profile.rhythm) * 0.003
            + (profile.sack_risk - 70) * (0.0035 if pressured else 0.0015)
            + max(0.0, 45 - profile.checkdown_willingness) * 0.003
            - pressure_elapsed * 0.030,
            0.0,
            0.34 if not pressured else 0.28,
        )
        if tick < self.config.max_ticks - 5 and self.rng.random() < hold_chance:
            return None
        return weighted_choice(
            self.rng,
            [
                (
                    route,
                    max(
                        0.05,
                        (
                            route.final_open_score * 1.8
                            + route.target_priority * 0.036
                            + max(0.0, 5.0 - route.read_rank) * 0.13
                            - max(0, route.depth) * (0.004 if concept == "deep" and not pressured else 0.010)
                            + max(0.0, profile.deep_aggression - 50) * (0.018 if route.depth >= 10 else 0.002)
                            + max(0.0, profile.checkdown_willingness - 50) * (0.016 if route.depth <= 6 else -0.002)
                            + max(0.0, profile.broken_play_creation - 60) * (0.014 if pressured and route.depth >= 8 else 0.0)
                        )
                        * self.concept_fit(route, concept, pressured, profile),
                    ),
                )
                for route in open_routes
            ],
        )

    def completion_probability(
        self,
        qb: PlayerSnapshot,
        route: RouteTickState,
        pressured: bool,
        field_pos: int,
        profile: QBBehaviorProfile,
        throw_tick: int,
    ) -> tuple[float, float]:
        air_yards = max(0, min(route.depth, 100 - field_pos))
        accuracy_key = "pass_accuracy_short"
        if air_yards >= 18:
            accuracy_key = "pass_accuracy_deep"
        elif air_yards >= 9:
            accuracy_key = "pass_accuracy_mid"
        qb_score = average([qb.rating(accuracy_key), qb.rating("platform_control"), qb.rating("composure"), qb.rating("throw_release")])
        receiver_score = weighted_average(route.receiver, RECEIVER_WEIGHTS)
        coverage_score = weighted_average(route.defender, COVERAGE_WEIGHTS)
        depth_penalty = max(0, air_yards - 5) * 0.014
        completion = 0.610 + (qb_score - 65) * 0.0037 + (receiver_score - coverage_score) * 0.0024
        completion += (route.final_open_score - self.config.open_threshold) * 0.075
        completion -= depth_penalty
        if pressured:
            completion -= 0.105
            completion -= max(0.0, 62 - profile.rhythm) * 0.0015
            completion += max(0.0, profile.broken_play_creation - 70) * 0.0009
        if profile.pocket_drift >= 70 and throw_tick >= route.break_tick:
            completion -= (profile.pocket_drift - 69) * 0.0010
        if air_yards >= 12:
            completion -= max(0.0, profile.deep_aggression - 72) * 0.0007
        completion = clamp(completion, 0.18, 0.88)

        interception = 0.010 + max(0, air_yards - 8) * 0.00075
        interception += max(0, coverage_score - qb_score) * 0.00035
        interception += max(0, 62 - qb.rating("discipline")) * 0.00018
        interception -= max(0, route.final_open_score - self.config.open_threshold) * 0.003
        interception += max(0.0, profile.deep_aggression - 65) * 0.00018
        interception += max(0.0, 50 - profile.checkdown_willingness) * 0.00010
        if pressured:
            interception += 0.011
        return float(completion), float(clamp(interception, 0.002, 0.070))

    def yac_yards(self, route: RouteTickState, defense: TeamSnapshot, concept: str) -> int:
        yac_score = weighted_average(route.receiver, YAC_WEIGHTS)
        tackle_score = defense.tackling_score()
        base = {
            "screen": 5.9,
            "quick": 3.4,
            "short": 3.0,
            "intermediate": 2.0,
            "deep": 1.0,
        }[concept]
        yac = max(0, int(round(self.rng.gauss(base + (yac_score - tackle_score) * 0.035, 3.1))))
        if self.rng.random() < clamp((yac_score - tackle_score) * 0.0012 + 0.022, 0.006, 0.082):
            yac += int(round(self.rng.lognormvariate(1.82, 0.35)))
        return yac

    def scramble_yards(
        self,
        qb: PlayerSnapshot,
        profile: QBBehaviorProfile,
        pressure_score: float,
        field_pos: int,
        distance: int,
        *,
        broken_pressure: bool = False,
    ) -> tuple[int, float]:
        escape_score = weighted_average(qb, QB_SCRAMBLE_WEIGHTS)
        base = (
            3.5
            + (escape_score - 65) * 0.075
            + (profile.pressure_escape - 50) * 0.040
            + (profile.broken_play_creation - 50) * 0.030
            + (profile.scramble_trigger - 50) * 0.020
            - pressure_score * 0.018
        )
        if broken_pressure:
            base += 2.2
        yards = int(round(self.rng.gauss(base, 3.6)))
        if self.rng.random() < clamp((escape_score - 66) * 0.0035 + (profile.broken_play_creation - 60) * 0.0020 + 0.035, 0.006, 0.28):
            yards += int(round(self.rng.lognormvariate(1.55, 0.32)))
        yards = int(clamp(yards, -2, min(22, max(0, 100 - field_pos))))
        return yards, escape_score

    def resolve_pass(
        self,
        offense: TeamSnapshot,
        defense: TeamSnapshot,
        *,
        down: int,
        distance: int,
        field_pos: int,
        concept: str | None = None,
    ) -> TickPassResult:
        qb = offense.starter("QB")
        profile = qb_behavior_profile(qb)
        concept = concept if concept in PASS_CONCEPTS else self.choose_concept(down, distance, field_pos, profile)
        routes = self.build_routes(offense, defense, concept, field_pos, distance)
        pressure_tick, pressure_score = self.pressure_arrival_tick(offense, defense, concept, qb, profile)
        decision_interval = self.qb_decision_interval(qb, profile)
        events: list[TickEvent] = [
            TickEvent(0, 0.0, "snap", f"{qb.name} takes the snap in a {concept} concept.", {"qb_style": profile.label}),
        ]
        rusher = self.select_rusher(defense)
        throw_route: RouteTickState | None = None
        throw_tick: int | None = None
        sack_tick: int | None = None
        pressured = False
        scramble_checked = False
        best_open_score = 0.0

        for tick in range(1, self.config.max_ticks + 1):
            for route in routes:
                self.update_route(route, tick)
            best_open_score = max(best_open_score, max(route.final_open_score for route in routes))
            if self.config.debug_ticks:
                best_route = max(routes, key=lambda route: route.final_open_score)
                events.append(
                    TickEvent(
                        tick,
                        tick * self.config.tick_seconds,
                        "tick_state",
                        f"Best route: {best_route.receiver.name} at {best_route.final_open_score:.2f}.",
                        {
                            "best_receiver": best_route.receiver.name,
                            "best_open_score": round(best_route.final_open_score, 2),
                            "routes": [
                                {
                                    "receiver": route.receiver.name,
                                    "defender": route.defender.name,
                                    "depth": route.depth,
                                    "break_tick": route.break_tick,
                                    "separation": round(route.separation, 2),
                                    "open_score": round(route.final_open_score, 2),
                                }
                                for route in routes
                            ],
                        },
                    )
                )
            if pressure_tick is not None and tick == pressure_tick:
                pressured = True
                events.append(TickEvent(tick, tick * self.config.tick_seconds, "pressure", f"{rusher.name} compresses the pocket.", {"pressure_score": round(pressure_score, 2)}))

            pressure_elapsed = tick - pressure_tick if pressured and pressure_tick is not None else 0

            if pressured and pressure_tick is not None and pressure_elapsed >= self.config.scramble_after_pressure_ticks and not scramble_checked:
                emergency_route = self.choose_throw_target(routes, tick, True, concept, profile, pressure_elapsed)
                if emergency_route:
                    throw_route = emergency_route
                    throw_tick = tick
                    event_kind = "broken_play_throw" if profile.broken_play_creation >= 78 and profile.checkdown_willingness < 56 else "hot_throw"
                    event_desc = (
                        f"{qb.name} extends the play and throws to {throw_route.receiver.name}."
                        if event_kind == "broken_play_throw"
                        else f"{qb.name} gets the ball out under pressure to {throw_route.receiver.name}."
                    )
                    events.append(
                        TickEvent(
                            tick,
                            tick * self.config.tick_seconds,
                            event_kind,
                            event_desc,
                            {
                                "separation": round(throw_route.separation, 2),
                                "open_score": round(throw_route.final_open_score, 2),
                                "depth": throw_route.depth,
                            },
                        )
                    )
                    break
                scramble_checked = True
                throwaway_chance = clamp(
                    (profile.throwaway_discipline - 55) * 0.006
                    + (profile.pocket_discipline - 60) * 0.002
                    - (profile.deep_aggression - 60) * 0.002
                    - (profile.broken_play_creation - 65) * 0.0015,
                    0.0,
                    0.46,
                )
                if self.rng.random() < throwaway_chance:
                    desc = f"{qb.name} throws it away under pressure."
                    events.append(TickEvent(tick, tick * self.config.tick_seconds, "throwaway", desc, {"throwaway_chance": round(throwaway_chance, 3)}))
                    return TickPassResult(
                        concept=concept,
                        outcome="throwaway",
                        yards=0,
                        air_yards=0,
                        yac_yards=0,
                        ticks_elapsed=tick,
                        time_elapsed_seconds=round(tick * self.config.tick_seconds, 2),
                        quarterback=qb,
                        qb_profile=profile,
                        target=None,
                        defender=rusher,
                        rusher=rusher,
                        throw_tick=tick,
                        pressure_tick=pressure_tick,
                        sack_tick=None,
                        completion_probability=0.0,
                        interception_probability=0.0,
                        pressure_score=pressure_score,
                        best_open_score=best_open_score,
                        description=desc,
                        routes=routes,
                        events=events,
                    )

                scramble_yards, escape_score = self.scramble_yards(qb, profile, pressure_score, field_pos, distance)
                scramble_chance = clamp(
                    0.16
                    + (escape_score - 65) * 0.006
                    + (profile.scramble_trigger - 50) * 0.004
                    + (profile.pocket_drift - 50) * 0.002
                    - pressure_score * 0.002,
                    0.04,
                    0.72,
                )
                if self.rng.random() < scramble_chance:
                    events.append(
                        TickEvent(
                            tick,
                            tick * self.config.tick_seconds,
                            "scramble",
                            f"{qb.name} escapes pressure and scrambles for {scramble_yards}.",
                            {"escape_score": round(escape_score, 2), "yards": scramble_yards},
                        )
                    )
                    return TickPassResult(
                        concept=concept,
                        outcome="scramble",
                        yards=scramble_yards,
                        air_yards=0,
                        yac_yards=0,
                        ticks_elapsed=tick,
                        time_elapsed_seconds=round(tick * self.config.tick_seconds, 2),
                        quarterback=qb,
                        qb_profile=profile,
                        target=None,
                        defender=rusher,
                        rusher=rusher,
                        throw_tick=None,
                        pressure_tick=pressure_tick,
                        sack_tick=None,
                        completion_probability=0.0,
                        interception_probability=0.0,
                        pressure_score=pressure_score,
                        best_open_score=best_open_score,
                        description=f"{qb.name} scrambles for {scramble_yards}.",
                        routes=routes,
                        events=events,
                    )

            if pressure_tick is not None and tick >= pressure_tick + self.config.sack_after_pressure_ticks:
                escape_score = average([qb.rating("speed"), qb.rating("acceleration"), qb.rating("agility"), qb.rating("processing_speed")])
                sack_chance = clamp(
                    0.42
                    + pressure_score * 0.010
                    - (escape_score - 65) * 0.003
                    + (profile.sack_risk - 50) * 0.004
                    - (profile.pressure_escape - 50) * 0.002
                    - (profile.throwaway_discipline - 50) * 0.0015,
                    0.08,
                    0.86,
                )
                if self.rng.random() < sack_chance:
                    broken_pressure_chance = clamp(
                        (profile.pressure_escape - 55) * 0.006
                        + (profile.broken_play_creation - 60) * 0.003
                        - (profile.sack_risk - 65) * 0.0025,
                        0.01,
                        0.45,
                    )
                    if self.rng.random() < broken_pressure_chance:
                        scramble_yards, escape_score = self.scramble_yards(
                            qb,
                            profile,
                            pressure_score,
                            field_pos,
                            distance,
                            broken_pressure=True,
                        )
                        events.append(
                            TickEvent(
                                tick,
                                tick * self.config.tick_seconds,
                                "broken_pressure",
                                f"{qb.name} breaks out of pressure and turns it into {scramble_yards}.",
                                {"escape_score": round(escape_score, 2), "yards": scramble_yards},
                            )
                        )
                        return TickPassResult(
                            concept=concept,
                            outcome="scramble",
                            yards=scramble_yards,
                            air_yards=0,
                            yac_yards=0,
                            ticks_elapsed=tick,
                            time_elapsed_seconds=round(tick * self.config.tick_seconds, 2),
                            quarterback=qb,
                            qb_profile=profile,
                            target=None,
                            defender=rusher,
                            rusher=rusher,
                            throw_tick=None,
                            pressure_tick=pressure_tick,
                            sack_tick=None,
                            completion_probability=0.0,
                            interception_probability=0.0,
                            pressure_score=pressure_score,
                            best_open_score=best_open_score,
                            description=f"{qb.name} breaks pressure and scrambles for {scramble_yards}.",
                            routes=routes,
                            events=events,
                        )
                    sack_tick = tick
                    loss_mean = (
                        6.2
                        + pressure_score * 0.035
                        + (profile.sack_risk - 50) * 0.080
                        + (profile.pocket_drift - 50) * 0.040
                        - (profile.throwaway_discipline - 50) * 0.030
                    )
                    loss = int(clamp(round(self.rng.gauss(loss_mean, 2.4)), 1, 20))
                    events.append(TickEvent(tick, tick * self.config.tick_seconds, "sack", f"{rusher.name} gets home before the throw.", {"loss": loss}))
                    return TickPassResult(
                        concept=concept,
                        outcome="sack",
                        yards=-loss,
                        air_yards=0,
                        yac_yards=0,
                        ticks_elapsed=tick,
                        time_elapsed_seconds=round(tick * self.config.tick_seconds, 2),
                        quarterback=qb,
                        qb_profile=profile,
                        target=None,
                        defender=rusher,
                        rusher=rusher,
                        throw_tick=None,
                        pressure_tick=pressure_tick,
                        sack_tick=sack_tick,
                        completion_probability=0.0,
                        interception_probability=0.0,
                        pressure_score=pressure_score,
                        best_open_score=best_open_score,
                        description=f"{rusher.name} sacks {qb.name} for a loss of {loss}.",
                        routes=routes,
                        events=events,
                    )

            if tick < self.config.decision_tick_floor:
                continue
            if tick % decision_interval != 0 and not (pressured and pressure_tick and tick >= pressure_tick + self.config.throw_after_pressure_ticks):
                continue
            throw_route = self.choose_throw_target(routes, tick, pressured, concept, profile, pressure_elapsed)
            if throw_route:
                throw_tick = tick
                events.append(
                    TickEvent(
                        tick,
                        tick * self.config.tick_seconds,
                        "throw",
                        f"{qb.name} throws to {throw_route.receiver.name}.",
                        {
                            "separation": round(throw_route.separation, 2),
                            "open_score": round(throw_route.final_open_score, 2),
                            "depth": throw_route.depth,
                        },
                    )
                )
                break

        if throw_route is None:
            throw_route = max(routes, key=lambda route: route.final_open_score + route.target_priority * 0.012)
            throw_tick = self.config.max_ticks
            events.append(
                TickEvent(
                    throw_tick,
                    throw_tick * self.config.tick_seconds,
                    "late_throw",
                    f"{qb.name} runs out of clean answers and forces it toward {throw_route.receiver.name}.",
                    {"open_score": round(throw_route.final_open_score, 2), "depth": throw_route.depth},
                )
            )

        completion, interception = self.completion_probability(qb, throw_route, pressured, field_pos, profile, throw_tick)
        air_yards = max(0, min(throw_route.depth, 100 - field_pos))
        roll = self.rng.random()
        if roll < interception:
            events.append(TickEvent(throw_tick, throw_tick * self.config.tick_seconds, "interception", f"{throw_route.defender.name} undercuts the throw."))
            return TickPassResult(
                concept=concept,
                outcome="interception",
                yards=0,
                air_yards=air_yards,
                yac_yards=0,
                ticks_elapsed=throw_tick,
                time_elapsed_seconds=round(throw_tick * self.config.tick_seconds, 2),
                quarterback=qb,
                qb_profile=profile,
                target=throw_route.receiver,
                defender=throw_route.defender,
                rusher=rusher if pressured else None,
                throw_tick=throw_tick,
                pressure_tick=pressure_tick,
                sack_tick=None,
                completion_probability=completion,
                interception_probability=interception,
                pressure_score=pressure_score,
                best_open_score=best_open_score,
                description=f"{qb.name} is intercepted by {throw_route.defender.name} targeting {throw_route.receiver.name}.",
                routes=routes,
                events=events,
            )
        if roll > completion:
            breakup_chance = clamp(0.25 + (weighted_average(throw_route.defender, COVERAGE_WEIGHTS) - weighted_average(throw_route.receiver, RECEIVER_WEIGHTS)) * 0.004, 0.12, 0.58)
            if self.rng.random() < breakup_chance:
                desc = f"{qb.name}'s pass for {throw_route.receiver.name} is broken up by {throw_route.defender.name}."
                events.append(TickEvent(throw_tick, throw_tick * self.config.tick_seconds, "breakup", desc))
            else:
                desc = f"{qb.name}'s pass for {throw_route.receiver.name} falls incomplete."
                events.append(TickEvent(throw_tick, throw_tick * self.config.tick_seconds, "incomplete", desc))
            return TickPassResult(
                concept=concept,
                outcome="incompletion",
                yards=0,
                air_yards=air_yards,
                yac_yards=0,
                ticks_elapsed=throw_tick,
                time_elapsed_seconds=round(throw_tick * self.config.tick_seconds, 2),
                quarterback=qb,
                qb_profile=profile,
                target=throw_route.receiver,
                defender=throw_route.defender,
                rusher=rusher if pressured else None,
                throw_tick=throw_tick,
                pressure_tick=pressure_tick,
                sack_tick=None,
                completion_probability=completion,
                interception_probability=interception,
                pressure_score=pressure_score,
                best_open_score=best_open_score,
                description=desc,
                routes=routes,
                events=events,
            )

        yac = self.yac_yards(throw_route, defense, concept)
        yards = int(clamp(air_yards + yac, 0, max(0, 100 - field_pos)))
        events.append(TickEvent(throw_tick, throw_tick * self.config.tick_seconds, "completion", f"{throw_route.receiver.name} secures it for {yards} yards.", {"air_yards": air_yards, "yac": yac}))
        return TickPassResult(
            concept=concept,
            outcome="completion",
            yards=yards,
            air_yards=air_yards,
            yac_yards=yac,
            ticks_elapsed=throw_tick,
            time_elapsed_seconds=round(throw_tick * self.config.tick_seconds, 2),
            quarterback=qb,
            qb_profile=profile,
            target=throw_route.receiver,
            defender=throw_route.defender,
            rusher=rusher if pressured else None,
            throw_tick=throw_tick,
            pressure_tick=pressure_tick,
            sack_tick=None,
            completion_probability=completion,
            interception_probability=interception,
            pressure_score=pressure_score,
            best_open_score=best_open_score,
            description=f"{qb.name} completes to {throw_route.receiver.name} for {yards}.",
            routes=routes,
            events=events,
        )


def resolve_pass_tick(
    offense: TeamSnapshot,
    defense: TeamSnapshot,
    *,
    down: int,
    distance: int,
    field_pos: int,
    concept: str | None = None,
    seed: int | None = None,
    config: TickConfig | None = None,
) -> TickPassResult:
    rng = random.Random(seed) if seed is not None else random.Random()
    return TickPassResolver(rng=rng, config=config).resolve_pass(
        offense,
        defense,
        down=down,
        distance=distance,
        field_pos=field_pos,
        concept=concept,
    )
