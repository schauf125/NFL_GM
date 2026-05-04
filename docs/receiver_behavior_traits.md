# Receiver Behavior Traits

These traits describe how WRs and TEs behave inside the sim engine and future
tick pass engine. They are football style traits, not hidden personality traits,
and all values are 0-100 where 50 is neutral.

## Fields

- `target_gravity`: how strongly the receiver earns designed and progression targets.
- `release_urgency`: how quickly the receiver wants to win off the line.
- `route_pacing`: timing, pacing, and landmark discipline in the route.
- `vertical_intent`: how much the receiver stresses downfield throws.
- `middle_comfort`: willingness and reliability working between the numbers.
- `contested_alpha`: how much the receiver invites and wins tight-window targets.
- `sideline_awareness`: boundary/body-control value.
- `yac_intent`: how aggressively the receiver turns catches into run-after-catch yards.
- `scramble_drill`: ability to stay alive and uncover late in the down.
- `catch_security`: catch-point and post-catch ball security.

## Current Engine Usage

The match engine uses receiver behavior in six places:

- Pass concept weights for quick, short, intermediate, deep, and screen calls.
- Target share among WR/TE/RB receiving options.
- Air-yard tendency on deep and short concepts.
- Completion chance by concept and pressure state.
- Interception risk on aggressive/tight-window profiles.
- YAC and post-catch fumble chance.

## Rookie Generation

Generated WR/TE prospects receive `draft_prospect_receiver_behavior_profiles`
from their archetype and true ratings. When selected or converted to free
agency, that row copies into `player_receiver_behavior_profiles`.

Archetype examples:

- `Vertical threat`: more vertical intent and sideline usage.
- `Slot separator`: more route pacing, middle comfort, and scramble-drill value.
- `Possession target`: more catch security and chain-moving target gravity.
- `Contested-catch target`: more contested alpha and boundary trust.
- `Move tight end`: more route/YAC usage for a flex TE.
- `Inline tight end`: lower target gravity but higher middle/catch security.
- `Mismatch target`: more TE target gravity, contested alpha, and middle comfort.
- `Blocking specialist`: outlet-only receiving behavior.
