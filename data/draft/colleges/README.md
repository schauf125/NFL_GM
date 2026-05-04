# Draft College And Age Data

`college_pool.db` is generated from nflverse player college history and stores
weighted college names for fictional draft prospects.

Compound transfer strings are normalized to a single graduation/primary school.
Junior-college transfer fragments are dropped when a four-year school is
present, so generated prospects display one college only.

Age generation uses configurable draft-board buckets. Round 1 is mostly 21-22,
Rounds 2-3 still lean young but include more 23-24 prospects, Rounds 4-5 add
older breakout players, and Rounds 6-7 plus leftover prospects carry the largest
older tail. The bucket is currently based on preview rank and can later be driven
by actual player ratings or draft-board rank.

Regenerate from the project root:

```powershell
python tools\build_college_pool.py build
python tools\build_college_pool.py sample --count 20 --seed 2027
```
