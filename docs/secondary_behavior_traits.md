# Secondary Behavior Profiles

Secondary behavior profiles are football style modifiers for CB/NB/FS/SS/S players. They do not replace ratings. They help the sim engine make two similarly rated defensive backs feel different in coverage, tackle distribution, and turnover creation.

## Fields

- `press_timing`: Jam timing, patience, and contact discipline near the line.
- `man_mirror`: Ability to stay attached through routes in man coverage.
- `zone_eye_discipline`: Landmark spacing, route passing, and not chasing ghosts in zone.
- `break_trigger`: How quickly the defender plants and drives on the throw.
- `deep_range`: Range in post/split-safety duties and ability to overlap vertical routes.
- `ball_play_timing`: Interception/pass-breakup timing once the ball is in flight.
- `catch_point_compete`: Physicality and balance at contested catch points.
- `slot_traffic`: Comfort matching option routes, stacks, bunches, and inside run/pass traffic.
- `run_support_fit`: Alley fill, force support, and screen/run trigger behavior.
- `tackle_finish`: Open-field finishing and ability to turn contact into a solo tackle.
- `penalty_control`: DPI/illegal-contact/holding-style discipline modifier.

## Engine Use

- CB/NB/FS/SS slot selection receives style-specific bonuses.
- Team coverage score includes secondary man, zone, trigger, range, and ball timing.
- Pass plays adjust defender coverage score by concept: quick/short throws reward trigger and slot traffic, deep throws reward range and catch-point play.
- Interception and pass-breakup chances use ball timing, trigger, deep range, and catch-point compete.
- Run/pass tackle selection uses slot traffic, run support, range, and tackle finish.
- Team discipline uses secondary penalty control.

## Generated Rookies

Generated CB/NB/FS/SS/S prospects receive `draft_prospect_secondary_behavior_profiles` from their archetype:

- `Man corner`: mirror/press profile.
- `Zone corner`: eyes, trigger, and ball timing.
- `Slot corner`: slot traffic, quick trigger, and support.
- `Deep safety`: deep range and ball play.
- `Box safety`: run support and tackle finish.
- `Versatile safety`: balanced range, slot, and support.

When a drafted or undrafted prospect becomes a player, that row copies into `player_secondary_behavior_profiles`.
