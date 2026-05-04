# RB Behavior Traits

These traits describe how RBs and FBs behave inside the sim engine and future
tick run engine. They are football style traits, not hidden personality traits,
and all values are 0-100 where 50 is neutral.

## Fields

- `early_down_gravity`: how strongly the back pulls routine carries and workhorse usage.
- `patience`: willingness to let blocks develop before committing.
- `one_cut_decisiveness`: how quickly the back plants and hits the designed crease.
- `bounce_tendency`: how often the back tries to escape outside the designed lane.
- `home_run_hunting`: how aggressively the back chases explosive plays.
- `contact_appetite`: willingness and ability to lower pads into contact.
- `space_creation`: open-field and receiving-space creativity.
- `pass_game_usage`: outlet, screen, and receiving-down usage tendency.
- `short_yardage_trust`: goal-line and short-yardage carry trust.
- `ball_security_mindset`: fumble discipline beyond the raw `ball_security` rating.

## Current Engine Usage

The match engine uses RB behavior in five places:

- Carry share among RB depth-chart candidates.
- Run concept selection for inside zone, outside zone, power, and draw.
- Stuff and explosive-run chances.
- RB target share, especially on screens.
- Fumble event chance for RB/FB ball carriers.

## Rookie Generation

Generated RB/FB prospects receive `draft_prospect_rb_behavior_profiles` from
their archetype and true ratings. When selected or converted to free agency,
that row copies into `player_rb_behavior_profiles`.

Archetype examples:

- `Elusive back`: more bounce, home-run hunting, and space creation.
- `Power back`: more contact appetite and short-yardage trust.
- `Receiving back`: more pass-game usage and space creation.
- `One-cut back`: more patience and one-cut decisiveness.
- `Lead blocker`: fullback profile with contact and blocking/utility lean.
- `Short-yardage back`: fullback or big-back profile for conversion downs.
