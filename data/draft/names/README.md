# Draft Name Pool

`name_pool.db` is a generated SQLite database used by the draft generator.

It blends three sources:

- Social Security Administration national first-name counts.
- U.S. Census 2010 surname counts.
- nflverse player names for football-flavored first and last name components.
- `ethnicity_origins.json` for fictional prospect ethnicity targets,
  international-origin weights, and curated cultural name styles.

Regenerate it from the project root:

```powershell
python tools\build_name_pool.py build
python tools\build_name_pool.py sample --count 25 --seed 2027
python tools\build_name_pool.py sample --count 25 --seed 2027 --show-meta
```

The generator samples first and last names separately and avoids exact
past/present football player full names by default.

The ethnicity and origin fields are gameplay metadata for generated fictional
prospects. They are used to steer name flavor and class-level variety, not to
infer anything about real players.

Draft preview generation also applies soft position multipliers after sampling
the class-wide ethnicity mix. This keeps the whole class near the configured
targets while making positions feel different: QBs and specialists skew whiter,
RB/CB skew heavily Black, and Polynesian/Samoan-style origins are more common
on OL/DL/front-seven prospects. These position multipliers live in
`data/draft/generation/preview_config.json`.

Tune the demographic and international-origin weights in
`ethnicity_origins.json`, then rebuild `name_pool.db`.
