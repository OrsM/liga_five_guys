# Moves scored in SQL, not simulated (2026-10-17)

The board used to draw 3000 simulated seasons for every squad it tried
(every candidate, every combination the plan built) and read a move's gain
as the median of the paired differences and its chance as the share above
zero. Miguel asked for it in SQL ("this should be both simpler and faster").

A literal port is not that: the same call as one set-based SQLite statement
over the draws took 284s against numpy's 0.44s. What SQL does well is the
closed form: a forecast cell (points if he plays `pts`, chance `p`) drawn as
the Bootstrap draws it has mean `p*pts` and match variance
`p*pts^2*E[pool^2]/mean(pool)^2 - (p*pts)^2`; the persistent rate (sd
`share`) multiplies all of a player's jornadas together. A move changes
some (player, jornada) cells of your eleven by +-1, so its gain has mean
`sum a_k` and variance `(1+share^2)*sum v + share^2*sum a_k^2`, `a_k` a
player's summed mean change. `ffcore/gain.py` does the elevens (the one
rule) and these sums in one query; the chance is the normal's.

Checked before switching, on all 140 moves ranked at past locks:
the same verdict on every one (22 worth doing, 118 not); `p_better` within
0.01 on average (the simulation's own noise at 3000 trials is 0.009);
gain 2-3% above the simulation's, which was a median (points are skewed).
The 3 moves more than 0.05 apart barely change the eleven (a point mass at
0 the simulation counted as ties); all were rejected either way.

What changed on the golden boards: numbers by a few percent, and on
2026-09-12 (in debt) the sales that clear the debt: Mantilla's sale cost 0
in the median and 0.05 on average, so the cheapest cover became Houary +
Radu instead of four sales.

The standings went the same way the next day (Miguel: "why do we need that
other simulation?"). A manager's final points are his eleven's sum, mean
and spread as above; managers' players are their own, so "he ends above
you" is the normal of the difference, expected finish is 1 plus those, and
the chance of each position integrates the others over your own total
(`Standings.position`). On the live board of 2026-10-17 against 3000
simulated seasons: means within 3 points, spreads within 2%, each "above
you" within 0.011, p_win 0.233 against 0.240, expected finish 1.903 against
1.889: the simulation's own noise. The golden boards' standings moved by
the same amounts; no plan or verdict changed. `ffcore/season.py` keeps only
the league state; the eleven rule is gain.ELEVEN alone (the phone's line-up uses it too).

Both ways share the same assumptions (players independent, one persistent
error each), so neither corrects p_win's suspected overconfidence; the
calibration check (`sim_calibration`) now tests the formula's spread.
