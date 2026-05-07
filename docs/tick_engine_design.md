# Tick Engine Prototype

This document tracks the first base build of the 0.1-second tick system.

## Current Scope

- File: `engine/tick_engine.py`
- Tool: `tools/tick_playtest.py`
- Active-save wrapper: `python tools\play.py tick-playtest ...`
- Status: prototype only; not wired into `engine/match_engine.py` game simulation.

The current vertical slice resolves one passing play with 0.1-second ticks. It reads existing `TeamSnapshot` and `PlayerSnapshot` objects, uses the same rating keys as the match engine, and returns a structured `TickPassResult` plus route and event logs.

## Architecture Decisions

- Tick logs are debug-only. Normal output stores meaningful play events such as snap, pressure, throw, hot throw, scramble, sack, completion, breakup, interception, and late throw. Full per-tick route state is available through `TickConfig(debug_ticks=True)` or `tools\tick_playtest.py --debug-ticks`.
- The first version uses scalar route separation, pressure timing, and open-score values rather than true X/Y player coordinates. This keeps the ratings model testable before committing to a full spatial physics layer.
- Routes are generated from broad concepts for now. Real playbook route combinations can replace the concept generator later.
- `match_engine.py` should remain the orchestrator for drives, clock, scoring, penalties, special teams, stats, and persistence. The tick engine should resolve play physics and return a result that can be translated into the current play contract.
- Stamina, fatigue, durability, and injuries are deferred.

## Pass-Play Slice

The prototype models:

- QB decision cadence from processing speed, play recognition, composure.
- QB behavior profiles from `engine\qb_behavior.py`, including rhythm, pocket drift, checkdown willingness, deep aggression, pressure escape, broken-play creation, sack risk, and throwaway discipline. Real QBs use named overrides or stored player profiles; generated rookie QBs get draft-prospect behavior profiles that copy into player profiles when selected.
- RB/FB behavior profiles from `engine\rb_behavior.py`, including carry gravity, patience, one-cut decisiveness, bounce tendency, home-run hunting, contact appetite, pass-game usage, short-yardage trust, and ball-security mindset. These now influence match-engine run concept choice, carry share, run volatility, receiving-back target share, and fumble risk; future tick runs should use the same profiles for per-tick lane decisions.
- WR/TE behavior profiles from `engine\receiver_behavior.py`, including target gravity, release urgency, route pacing, vertical intent, middle comfort, contested alpha, sideline awareness, YAC intent, scramble-drill value, and catch security. These influence match-engine pass concept choice, target share, air-yards tendency, completion chance, YAC, interception risk, and post-catch fumbles.
- OL behavior profiles from `engine\ol_behavior.py`, including pass-set patience, mirror, anchor, hand timing, stunt awareness, drive finish, reach range, combo timing, second-level climb, and penalty control. These influence match-engine pass/run block strength, run concept fit, and offensive-line discipline.
- Edge behavior profiles from `engine\edge_behavior.py`, including getoff timing, speed arc, power collapse, counter plan, stunt timing, contain discipline, run squeeze, backside pursuit, finish skill, and rush discipline. These influence match-engine pass rush strength, sack credit, run contain, and defensive discipline.
- IDL behavior profiles from `engine\idl_behavior.py`, including getoff timing, penetration burst, power collapse, double-team anchor, gap control, shed timing, stunt timing, counter plan, finish skill, and rush discipline. These influence match-engine interior rush, sack credit, run defense, and defensive discipline.
- LB behavior profiles from `engine\lb_behavior.py`, including trigger quickness, gap-fit discipline, scrape range, traffic navigation, zone depth, man-match carry, blitz timing, tackle finish, rally support, and penalty control. These influence match-engine LB slot fit, run defense, coverage, blitz pressure, tackle distribution, and defensive discipline.
- Secondary behavior profiles from `engine\secondary_behavior.py`, including press timing, man mirror, zone eyes, break trigger, deep range, ball timing, catch-point compete, slot traffic, run support, tackle finish, and penalty control. These influence DB slot fit, coverage scoring, pass breakups, interceptions, tackle distribution, and defensive discipline.
- Specialist behavior profiles from `engine\specialist_behavior.py`, including kick operation, kickoff control, punt hang time, punt placement, snap accuracy, lane release, gunner speed, return-lane vision, block timing, coverage tackle, and penalty control. These lightly influence K/P/LS slot fit, field goals, extra points, punts, kickoffs, return yards, block chances, and core special-teams snap selection.
- Route depths and break timing by concept.
- Coverage matchups by offensive slot and receiver type so outside WRs draw corners, slot WRs usually draw nickels, and backs/TEs draw linebackers or safeties before fallback coverage.
- Concept-aware read progression, including primary/secondary/outlet route roles and target priority bonuses for better receivers and intended concept depths.
- Receiver release, route timing, route snap, and separation.
- Coverage using press, man/zone-style coverage weights, agility, and play recognition.
- Pass rush versus pass protection with pressure arrival ticks.
- Pressure behavior with hot throws, broken-play throws, throwaways, mobile-QB scrambles, broken pressure escapes, and sacks after pressure.
- Target choice when a route becomes open inside the current read window.
- Completion, interception, breakup, and YAC probabilities.

## Current Return Shape

`TickPassResult` includes:

- concept and outcome
- yards, air yards, YAC
- elapsed ticks and seconds
- QB, QB behavior profile, target, defender, rusher
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
