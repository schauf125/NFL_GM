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
    RECEIVER_WEIGHTS,
    TACKLE_WEIGHTS,
    YAC_WEIGHTS,
    PlayerSnapshot,
    TeamSnapshot,
    average,
    clamp,
    weighted_average,
    weighted_choice,
)


PASS_CONCEPTS = ("screen", "quick", "short", "intermediate", "deep")


@dataclass(frozen=True)
class TickConfig:
    tick_seconds: float = 0.1
    max_ticks: int = 60
    decision_tick_floor: int = 7
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

    def choose_concept(self, down: int, distance: int, field_pos: int) -> str:
        red_zone = field_pos >= 80
        return weighted_choice(
            self.rng,
            [
                ("screen", 0.55 if distance >= 7 else 0.35),
                ("quick", 1.35 if distance <= 4 or red_zone else 0.90),
                ("short", 1.15),
                ("intermediate", 1.00 if distance >= 5 else 0.55),
                ("deep", 0.32 if red_zone else 0.45 if down < 3 else 0.70),
            ],
        )

    def select_rusher(self, defense: TeamSnapshot) -> PlayerSnapshot:
        rushers = defense.defensive_front() or defense.roster[:8]
        return weighted_choice(self.rng, [(player, weighted_average(player, PASS_RUSH_WEIGHTS)) for player in rushers])

    def select_defenders(self, defense: TeamSnapshot, count: int) -> list[PlayerSnapshot]:
        pool = defense.secondary() + defense.linebackers()
        if not pool:
            pool = defense.roster[:11]
        selected: list[PlayerSnapshot] = []
        used: set[int] = set()
        while len(selected) < count and len(used) < len(pool):
            defender = weighted_choice(
                self.rng,
                [
                    (player, weighted_average(player, COVERAGE_WEIGHTS))
                    for player in pool
                    if player.player_id not in used
                ],
            )
            selected.append(defender)
            used.add(defender.player_id)
        while len(selected) < count:
            selected.append(weighted_choice(self.rng, [(player, weighted_average(player, COVERAGE_WEIGHTS)) for player in pool]))
        return selected

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

    def build_routes(self, offense: TeamSnapshot, defense: TeamSnapshot, concept: str, field_pos: int, distance: int) -> list[RouteTickState]:
        receivers = offense.unique_starters(["LWR", "RWR", "SWR", "TE", "RB"]) or offense.receiving_options()
        defenders = self.select_defenders(defense, len(receivers))
        depths = self.route_depths(concept, field_pos, distance)
        routes = []
        for idx, receiver in enumerate(receivers):
            defender = defenders[idx]
            depth = depths[idx % len(depths)]
            release = receiver.rating("release_vs_press")
            route_snap = receiver.rating("route_snap")
            route_timing = receiver.rating("route_timing")
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
            target_priority = weighted_average(receiver, RECEIVER_WEIGHTS) + (route_timing - 65) * 0.18 + (release - 65) * 0.10
            if idx == 0:
                target_priority += 4.0
            if concept == "screen" and receiver.position == "RB":
                target_priority += 8.0
            routes.append(
                RouteTickState(
                    receiver=receiver,
                    defender=defender,
                    depth=depth,
                    break_tick=break_tick,
                    target_priority=target_priority,
                )
            )
        return routes

    def pressure_arrival_tick(self, offense: TeamSnapshot, defense: TeamSnapshot, concept: str, qb: PlayerSnapshot) -> tuple[int | None, float]:
        pass_block = offense.pass_block_score()
        pass_rush = defense.pass_rush_score()
        qb_processing = average([qb.rating("processing_speed"), qb.rating("play_recognition"), qb.rating("throw_release")])
        pressure_score = pass_rush - pass_block - (qb_processing - 65) * 0.20
        base = {
            "screen": 25,
            "quick": 24,
            "short": 27,
            "intermediate": 30,
            "deep": 33,
        }[concept]
        mean_tick = base - pressure_score * 0.18
        arrival = int(round(self.rng.gauss(mean_tick, 5.0)))
        chance = clamp(0.54 + pressure_score * 0.010, 0.16, 0.83)
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

    def qb_decision_interval(self, qb: PlayerSnapshot) -> int:
        processing = average([qb.rating("processing_speed"), qb.rating("play_recognition"), qb.rating("composure")])
        interval = round(5 - (processing - 50) / 18)
        return int(clamp(interval, 2, 6))

    def choose_throw_target(self, routes: list[RouteTickState], tick: int, pressured: bool, concept: str) -> RouteTickState | None:
        candidates = [route for route in routes if tick >= max(self.config.decision_tick_floor, route.break_tick - 3)]
        if not candidates:
            return None
        if concept == "deep" and not pressured and tick < 26:
            candidates = [route for route in candidates if route.depth >= 8]
        elif concept == "intermediate" and not pressured and tick < 20:
            candidates = [route for route in candidates if route.depth >= 5]
        if not candidates:
            return None
        threshold = self.config.open_threshold - (0.12 if pressured else 0.0)
        open_routes = [route for route in candidates if route.final_open_score >= threshold]
        if not open_routes:
            return None
        return weighted_choice(
            self.rng,
            [
                (
                    route,
                    max(0.05, route.final_open_score * 1.8 + route.target_priority * 0.030 - max(0, route.depth) * 0.010),
                )
                for route in open_routes
            ],
        )

    def completion_probability(self, qb: PlayerSnapshot, route: RouteTickState, pressured: bool, field_pos: int) -> tuple[float, float]:
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
        completion = clamp(completion, 0.18, 0.88)

        interception = 0.010 + max(0, air_yards - 8) * 0.00075
        interception += max(0, coverage_score - qb_score) * 0.00035
        interception += max(0, 62 - qb.rating("discipline")) * 0.00018
        interception -= max(0, route.final_open_score - self.config.open_threshold) * 0.003
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
        concept = concept if concept in PASS_CONCEPTS else self.choose_concept(down, distance, field_pos)
        routes = self.build_routes(offense, defense, concept, field_pos, distance)
        pressure_tick, pressure_score = self.pressure_arrival_tick(offense, defense, concept, qb)
        decision_interval = self.qb_decision_interval(qb)
        events: list[TickEvent] = [
            TickEvent(0, 0.0, "snap", f"{qb.name} takes the snap in a {concept} concept."),
        ]
        rusher = self.select_rusher(defense)
        throw_route: RouteTickState | None = None
        throw_tick: int | None = None
        sack_tick: int | None = None
        pressured = False
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

            if pressure_tick is not None and tick >= pressure_tick + self.config.sack_after_pressure_ticks:
                escape_score = average([qb.rating("speed"), qb.rating("acceleration"), qb.rating("agility"), qb.rating("processing_speed")])
                sack_chance = clamp(0.42 + pressure_score * 0.010 - (escape_score - 65) * 0.003, 0.12, 0.72)
                if self.rng.random() < sack_chance:
                    sack_tick = tick
                    loss = int(clamp(round(self.rng.gauss(6.2 + pressure_score * 0.035, 2.2)), 1, 15))
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
            throw_route = self.choose_throw_target(routes, tick, pressured, concept)
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

        completion, interception = self.completion_probability(qb, throw_route, pressured, field_pos)
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
