# Edge Behavior Profiles

Edge profiles describe how EDGE/OLB/DE defenders apply their ratings inside the sim engine. They are not user-facing personality traits; they are style controls for rush, contain, and finish behavior.

## Fields

- `getoff_timing`: snap burst and first-step timing.
- `speed_arc`: ability to win around the edge with speed and bend.
- `power_collapse`: ability to compress the pocket with power.
- `counter_plan`: rush sequencing, counters, and setup moves.
- `stunt_timing`: usefulness on twists, games, and simulated pressure calls.
- `contain_discipline`: outside leverage against runs, boots, and QB scrambles.
- `run_squeeze`: setting the edge and squeezing run lanes.
- `backside_pursuit`: chase-down range on backside runs and broken plays.
- `finish_skill`: conversion of pressure into sacks and strip-sack chances.
- `rush_discipline`: offsides/roughing/overrun control.

## Engine Usage

The match engine now uses edge profiles in four places:

- `pass_rush_score()` gets small modifiers from getoff, arc/power, counter plan, stunt timing, and finish.
- `sack_credit_weight()` nudges sack attribution toward rushers with better finish/counter/getoff profiles.
- `run_defense_score()` uses contain, run squeeze, and backside pursuit for edge players.
- `discipline_score()` blends edge rush discipline into team penalty behavior.

## Rookie Generation

Generated EDGE/OLB prospects receive `draft_prospect_edge_behavior_profiles` from their draft archetype:

- `Speed rusher`: more getoff, speed arc, backside pursuit, and finish; lighter run profile.
- `Power edge`: more power collapse, run squeeze, and finish.
- `Hybrid linebacker`: more contain, pursuit, stunt timing, and discipline.
- `Run-setting edge`: more contain, run squeeze, and rush discipline; lower pure arc/finish.

When a drafted or undrafted prospect becomes a player, that row copies into `player_edge_behavior_profiles`.
