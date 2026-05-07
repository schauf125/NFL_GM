# Draft Generation Config

`preview_config.json` stores tuning values for generated draft-class previews.

The code should treat this file as the tuning surface for position-aware
generation. It currently controls:

- generation version metadata
- position groups used by preview reports and metadata
- soft position-by-ethnicity assignment multipliers
- position-aware international country multipliers
- handedness and kicking-foot distribution

These values are gameplay metadata for fictional prospects. They should be tuned
for believable generated classes, not used to classify real players.
