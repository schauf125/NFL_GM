# IDL Behavior Profiles

IDL profiles describe how IDL/DT/NT defenders apply their ratings inside the sim engine. They are not user-facing personality traits; they are style controls for interior rush, run fits, double teams, and finish behavior.

## Fields

- `getoff_timing`: snap timing and first-step quickness.
- `penetration_burst`: one-gap burst and backfield disruption.
- `power_collapse`: bull rush, pocket push, and guard displacement.
- `double_team_anchor`: ability to absorb combo blocks without losing ground.
- `gap_control`: run-fit discipline and ability to keep the assigned gap clean.
- `block_shed_timing`: when and how well the defender comes off blocks.
- `stunt_timing`: usefulness on interior games, loops, and simulated pressure.
- `rush_counter_plan`: rush sequencing, counters, and hand usage.
- `finish_skill`: conversion of pressure into sacks and strip-sacks.
- `rush_discipline`: offsides/roughing/overrun control.

## Engine Usage

The match engine uses IDL profiles in four places:

- `pass_rush_score()` gets small modifiers from getoff, penetration, power collapse, stunt timing, counters, and finish.
- `sack_credit_weight()` separates true interior rushers from block-eating nose tackles.
- `run_defense_score()` uses double-team anchor, gap control, and block shed timing.
- `discipline_score()` blends IDL rush discipline into team penalty behavior.

## Rookie Generation

Generated IDL prospects receive `draft_prospect_idl_behavior_profiles` from their draft archetype:

- `Interior rusher`: more getoff, penetration, stunt timing, counters, and finish.
- `Nose tackle`: more double-team anchor, gap control, power, and block shed; lower finish.
- `Gap penetrator`: more getoff and penetration with lighter anchor.
- `Two-gapper`: more gap control, double-team anchor, shed timing, and discipline.

When a drafted or undrafted prospect becomes a player, that row copies into `player_idl_behavior_profiles`.
