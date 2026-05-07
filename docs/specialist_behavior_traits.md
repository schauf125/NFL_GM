# Specialist Behavior Profiles

Specialist behavior profiles are a light special-teams layer for K/P/LS players and core special teamers. They are not meant to turn special teams into a separate mini-engine. They give operation quality, coverage units, return units, and block specialists a small style signal.

## Fields

- `kick_operation`: Field-goal/extra-point repeatability, timing, and poise.
- `kickoff_control`: Kickoff leg, landing-zone control, and onside touch.
- `punt_hang_time`: Punter hang, coverage friendliness, and return suppression.
- `punt_placement`: Directional punting and pinning/fair-catch pressure.
- `snap_accuracy`: Long-snap operation quality.
- `lane_release`: Release timing into coverage lanes.
- `gunner_speed`: Punt/kick coverage burst and downfield speed.
- `return_lane_vision`: Return setup and return-unit lane feel.
- `block_timing`: Field-goal/punt block timing.
- `coverage_tackle`: Special-teams tackling reliability.
- `penalty_control`: Avoiding coverage flags and operation mistakes.

## Engine Use

- K/P/LS slot fit receives specialist-profile bonuses.
- Field goals and extra points use `kick_operation` plus long-snap/holder operation.
- Punt gross/fair-catch/return-yard outcomes use `punt_hang_time`, `punt_placement`, and coverage score.
- Kickoff placement, short kicks, out-of-bounds kicks, touchbacks, and returns use `kickoff_control`.
- Special-teams coverage units pull in high-scoring core teamers even if they are not defensive starters.
- Punt/field-goal block chances and blocker selection use `block_timing`.
- Returner selection uses `return_lane_vision`.

## Generated Rookies

Every generated draft prospect receives a `draft_prospect_specialist_behavior_profiles` row. K/P/LS archetypes get specialist-specific labels. Other positions get a modest special-teams profile from ratings, so a late-round safety, linebacker, receiver, or corner can still carry roster value as a coverage player, return helper, or block-team contributor.

When a drafted or undrafted prospect becomes a player, that row copies into `player_specialist_behavior_profiles`.
