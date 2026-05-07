# Draft Physical Profiles

`physical_profiles.db` is a generated SQLite database used to assign height,
weight, arm length, and hand size to draft prospects.

The profile builder uses nflverse player height/weight data, then layers in
combine/pro day arm-length and hand-size measurements from
`array-carpenter/nfl-draft-data`. It maps source positions into the sim's
position set and stores position-specific means, standard deviations, percentile
ranges, and body-measurement correlations.

Regenerate it from the project root:

```powershell
python tools\build_physical_profiles.py build
python tools\build_physical_profiles.py sample WR --count 20 --seed 2027
python tools\build_physical_profiles.py summary
```

Tune position mappings or hard generation bounds in `position_mapping.json`, then
rebuild `physical_profiles.db`.

Generated measurements are mostly Gaussian, but a small outlier chance is spread
across height, weight, arm length, and hand size. Arms are correlated mostly with
height; hands are correlated with both height and weight.
