# Tick Engine Prototype

This document tracks the first base build of the 0.1-second tick system.

## Current Scope

- File: `engine/tick_engine.py`
- Tool: `tools/tick_playtest.py`
- Active-save wrapper: `python tools\play.py tick-playtest ...`
- Status: prototype only; not wired into `engine/match_engine.py` game simulation.

The current vertical slice resolves one passing play with 0.1-second ticks. It reads existing `TeamSnapshot` and `PlayerSnapshot` objects, uses the same rating keys as the match engine, and returns a structured `TickPassResult` plus route and event logs.

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
- event log

## Intended Integration Path

1. Keep `match_engine.py` as the orchestrator for drives, scoring, penalties, clock, special teams, and persistence.
2. Wire tick pass resolution behind a feature flag.
3. Translate `TickPassResult` into the existing `pass_play` return contract.
4. Run `tools\sim_audit.py` against legacy pass resolution versus tick pass resolution.
5. Tune the tick slice before adding runs, collisions, and special teams.

## Open Architecture Questions

1. Should the final tick system log every tick, or only meaningful events by default with full tick traces available in debug mode?
2. Should player movement be represented as true X/Y coordinates immediately, or should this intermediate scalar model prove the ratings first?
3. Should play calls choose exact route combinations from playbook data, or should the engine synthesize routes from concepts for now?
4. Should coaching traits and play-caller tendencies affect route priority/throw timing in the tick engine, or stay outside in the orchestrator?
5. Should fatigue/stamina mutate during every tick, every play, or only at drive/series boundaries?
