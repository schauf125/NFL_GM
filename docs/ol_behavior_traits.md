# OL Behavior Traits

These traits describe how offensive tackles, guards, and centers behave inside
the sim engine and future tick blocking engine. They are football style traits,
not hidden personality traits, and all values are 0-100 where 50 is neutral.

## Fields

- `pass_set_patience`: ability to stay calm and avoid lunging in pass sets.
- `mirror_vs_speed`: lateral mirror and arc protection against speed.
- `anchor_vs_power`: ability to absorb bull rushes and compress-force rushes.
- `hand_timing`: strike timing, reset timing, and recovery with hands.
- `stunt_awareness`: recognition and passing off twists/games.
- `drive_finish`: downhill displacement and finish in the run game.
- `reach_range`: ability to seal laterally on zone and perimeter runs.
- `combo_timing`: timing with adjacent linemen on double teams and releases.
- `second_level_climb`: value climbing to linebackers and space targets.
- `penalty_control`: holding/false-start/illegal-block control beyond raw discipline.

## Current Engine Usage

The match engine uses OL behavior in four places:

- Pass-block score, especially mirror, anchor, hand timing, and stunt awareness.
- Run-block score, especially drive finish, reach range, combo timing, and climb.
- Run concept selection for inside zone, outside zone, power, and draw.
- Team discipline through OL penalty control.

## Rookie Generation

Generated OT/OG/C prospects receive `draft_prospect_ol_behavior_profiles` from
their archetype and true ratings. When selected or converted to free agency,
that row copies into `player_ol_behavior_profiles`.

Archetype examples:

- `Pass protector`: more pass-set patience, mirror, hand timing, and penalty control.
- `Drive blocker`: more drive finish, anchor, and combo timing.
- `Zone mover`: more reach range, second-level climb, and movement skills.
- `Anchor blocker`: more anchor, hand timing, and penalty control with less range.
