# Tick Engine Prototype

This document tracks the first base build of the 0.1-second tick system.

## Current Scope

- File: `engine/tick_engine.py`
- Tool: `tools/tick_playtest.py`
- Active-save wrapper: `python tools\play.py tick-playtest ...`
- Status: prototype only; not wired into `engine/match_engine.py` game simulation.

The current vertical slice resolves one passing play with 0.1-second ticks. It reads existing `TeamSnapshot` and `PlayerSnapshot` objects, uses the same rating keys as the match engine, and returns a structured `TickPassResult` plus route and event logs.

## Architecture Decisions

- Tick logs are debug-only. Normal output stores meaningful play events such as snap, pressure, throw, sack, completion, breakup, interception, and late throw. Full per-tick route state is available through `TickConfig(debug_ticks=True)` or `tools\tick_playtest.py --debug-ticks`.
- The first version uses scalar route separation, pressure timing, and open-score values rather than true X/Y player coordinates. This keeps the ratings model testable before committing to a full spatial physics layer.
- Routes are generated from broad concepts for now. Real playbook route combinations can replace the concept generator later.
- `match_engine.py` should remain the orchestrator for drives, clock, scoring, penalties, special teams, stats, and persistence. The tick engine should resolve play physics and return a result that can be translated into the current play contract.
- Stamina, fatigue, durability, and injuries are deferred.

## Pass-Play Slice

The prototype models:

- QB decision cadence from processing speed, play recognition, composure.
- Route depths and break timing by concept.
- Receiver release, route timing, route snap, and separation.
- Coverage using press, man/zone-style coverage weights, agility, and play recognition.
- Pass rush versus pass protection with pressure arrival ticks.
- Sacks after pressure.
- Target choice when a route becomes open.
- Completion, interception, breakup, and YAC probabilities.

## Current Return Shape

`TickPassResult` includes:

- concept and outcome
- yards, air yards, YAC
- elapsed ticks and seconds
- QB, target, defender, rusher
- throw tick, pressure tick, sack tick
- completion and interception probabilities
- route states
- meaningful event log
- optional full per-tick debug trace

## Intended Integration Path

1. Keep `match_engine.py` as the orchestrator for drives, scoring, penalties, clock, special teams, and persistence.
2. Wire tick pass resolution behind a feature flag.
3. Translate `TickPassResult` into the existing `pass_play` return contract.
4. Run `tools\sim_audit.py` against legacy pass resolution versus tick pass resolution.
5. Tune the tick slice before adding runs, collisions, and special teams.

## Open Architecture Questions

1. When tick passing is wired into `match_engine.py`, should it be enabled per game, per team, or globally by engine setting?
2. Should coaching traits and play-caller tendencies affect route priority/throw timing inside the tick engine, or should the orchestrator pre-select those tendencies before calling it?
3. What should the next tick slice be: designed runs, QB scrambles, or pass protection/receiver route packages with more playbook detail?
