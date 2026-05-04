# LB Behavior Profiles

LB profiles describe how ILB/LB/OLB defenders apply their ratings inside the sim engine. They are not user-facing personality traits; they are style controls for run fits, coverage drops, pursuit, blitzing, and tackling.

## Fields

- `trigger_quickness`: how quickly the LB diagnoses and attacks run/pass keys.
- `gap_fit_discipline`: run-fit reliability and ability to stay in the assigned gap.
- `scrape_range`: lateral range, pursuit, and sideline-to-sideline value.
- `traffic_navigation`: ability to work through bodies and avoid getting washed out.
- `zone_landmark_depth`: depth, spacing, and timing in zone drops.
- `man_match_carry`: ability to carry backs, tight ends, and crossers.
- `blitz_timing`: timing and usefulness as a pressure player.
- `tackle_finish`: solo tackle reliability.
- `rally_support`: assist tackle and group-tackle value.
- `penalty_control`: late-hit, illegal-contact, and assignment discipline.

## Engine Usage

The match engine uses LB profiles in these places:

- LB slot selection blends MLB/WLB/SLB fit from trigger, gap, range, coverage, blitz, and tackle behavior.
- `run_defense_score()` uses trigger, gap fit, scrape range, traffic navigation, and tackle finish.
- `coverage_score()` and coverage defender selection use zone depth, man-match carry, range, and rally support.
- `pass_rush_score()` gets a small blitz-pressure bonus from LB blitz timing.
- Tackle and assist distribution uses tackle finish, rally support, scrape range, and trigger behavior.
- `discipline_score()` blends LB penalty control into team penalty behavior.

## Rookie Generation

Generated ILB/LB/OLB prospects receive `draft_prospect_lb_behavior_profiles` from their archetype:

- `Coverage linebacker`: more zone/man coverage, range, rally support, and penalty control.
- `Box linebacker`: more trigger, gap fit, traffic navigation, tackle finish, and rally support.
- `Blitzer`: more blitz timing and range, with lower coverage and penalty control.
- `Hybrid linebacker`: balanced coverage, range, and blitz value for OLB prospects.
- Edge-style OLB archetypes receive pressure or run-fit LB profiles in addition to Edge profiles.

When a drafted or undrafted prospect becomes a player, that row copies into `player_lb_behavior_profiles`.
