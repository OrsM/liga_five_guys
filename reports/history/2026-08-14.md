# Fantasy report — 2026-08-14T2256Z

**Locks in 0h** · squad 171.50M · cash 63.29M · total 234.79M

## 1. Am I fielding the right eleven?

**Your XI: 4-5-1 · ≈35 pts expected next jornada** (uncalibrated — see the methodology link at the end)

| | Marked XI | Start% | xPts/j | State |
|---|---|--:|--:|---|
| POR | Ionut Radu | 90% | 5.9 | fit |
| DEF | Carl Starfelt | 60% | 3.1 | fit |
| DEF | Robin Le Normand | 60% | 2.6 | fit |
| DEF | Igor Zubeldia | 80% | 3.0 | fit |
| DEF | Omar El Hilali | 80% | 2.4 | fit |
| MED | Iñigo Ruiz de Galarreta | 60% | 2.7 | fit |
| MED | Dani Lorenzo | 90% | 2.6 | fit |
| MED | Pepelu | 70% | 3.0 | fit |
| MED | Jon Moncayola | 90% | 3.9 | fit |
| MED | Lucien Agoume | 80% | 3.2 | fit |
| DEL | Iñigo Vicente | 90% | 2.7 | fit |

**The model would score ≈35 — 0.3 pts/j more.** Its shape is 4-5-1.

| Bench this | For this | Worth |
|---|---|--:|
| Dani Lorenzo (2.6) | Ruben Garcia (2.9) | +0.3 |

_Swaps are same-position only: a cross-slot difference is a change of formation, not a substitution. Your own marks are the row above — this table is advice._

## 2. Is anyone injured, suspended, or doubtful?

**Nobody in your squad is flagged.** All 15 players with an entry on their team page read as available.

| Player | State | What the page says |
|---|---|---|
| Alvaro Fernandez | fit | listed, no flag |
| Beñat Turrientes | fit | listed, no flag |
| Carl Starfelt | fit | listed, no flag |
| Dani Lorenzo | fit | listed, no flag |
| Igor Zubeldia | fit | listed, no flag |
| Ionut Radu | fit | listed, no flag |
| Iñigo Ruiz de Galarreta | fit | listed, no flag |
| Iñigo Vicente | fit | listed, no flag |
| Jon Moncayola | fit | listed, no flag |
| Lucien Agoume | fit | listed, no flag |
| Omar El Hilali | fit | listed, no flag |
| Pepelu | fit | listed, no flag |
| Robin Le Normand | fit | listed, no flag |
| Ruben Garcia | fit | listed, no flag |
| Simon Eriksson | fit | listed, no flag |

_Read from the 'Estado físico', 'Sancionados' and 'No disponibles' blocks of each team page. A knock the site still lists as available (`Tocado`) is folded into doubt._

## 3. Is everyone expected to start?

**Every marked player is at 60% or above.**

| | Player | Start% | Reading |
|---|---|--:|---|
| XI | Dani Lorenzo | 90% | published |
| XI | Jon Moncayola | 90% | published |
| XI | Iñigo Vicente | 90% | published |
| XI | Ionut Radu | 90% | published |
| XI | Igor Zubeldia | 80% | published |
| XI | Omar El Hilali | 80% | published |
| XI | Lucien Agoume | 80% | published |
| XI | Pepelu | 70% | published |
| XI | Carl Starfelt | 60% | published |
| XI | Robin Le Normand | 60% | published |
| XI | Iñigo Ruiz de Galarreta | 60% | published |
| bench | Beñat Turrientes | 70% | published |
| bench | Ruben Garcia | 60% | published |
| bench | Simon Eriksson | 50% | published |
| bench | Alvaro Fernandez | 20% | published |

_`start_pct` is futbolfantasy's editorial bucket, read twice a day, not a live probability — it moved for only a handful of players across the snapshots taken so far. Threshold is `min_start` in `inputs/league.ini`._

## 4. Anything to do in the market?

_No slate pasted, so there is nothing you can bid on today that this report knows about. Paste today's market screenshot into the `seen` input to price it. Everyone unowned is ranked in `reports/watchlist.md`._

---

## Warnings

- **Data is 18h old** — the ingest workflow may have failed. Everything above is that snapshot.
- **Only 1 delantero** — one knock and you can't field a legal XI.
- **1 unmodelled** (Iñigo Vicente) — no LaLiga record, so they carry an assumed baseline, not an earned one.

_Compare squad value with the app; a mismatch means a name matched the wrong player. Roster read from the ledger._

## Not in your XI (as you have marked them)

**Gap** is what the XI loses per jornada if he has to play, after re-picking the formation. **€/pt** is value per expected point: the sell shortlist, worst first.

| Player | Pos | Value | xPts/j | Gap | €/pt | Why |
|---|---|--:|--:|--:|--:|---|
| Alvaro Fernandez | por | 4.73M | 0.9 | -5.1 | 5.42M | 3rd POR — only 1 can ever play |
| Ruben Garcia | med | 13.75M | 2.9 | +0.0 | 4.77M | rising — sell into strength |
| Beñat Turrientes | med | 7.06M | 2.5 | -0.2 | 2.81M | 7th MED — only 5 can ever play |
| Simon Eriksson | por | 2.92M | 2.2 | -3.8 | 1.34M | 2nd POR — only 1 can ever play |

_Selling to the app pays the value give or take 12%: the 13 priced sales in the ledger went median +3.3%, -9.4% to +12.0% (n=13), so read every value above as that band, not as a number. Who is short in this position, and who can still afford you, is in `reports/rivals.md`._

## Your movers (24h, over 1%)

| Player | Value | 24h | % |
|---|--:|--:|--:|
| Jon Moncayola | 6.79M | 107K | +1.61% |
| Robin Le Normand | 9.98M | 140K | +1.43% |
| Pepelu | 7.47M | 99K | +1.34% |
| Ruben Garcia | 13.75M | 173K | +1.27% |
| Beñat Turrientes | 7.06M | 78K | +1.12% |
| Ionut Radu | 39.25M | -464K | -1.17% |
| Alvaro Fernandez | 4.73M | -68K | -1.42% |

## Notes

_621 players tracked, 512 with a probable-XI reading._

_xPts/j — expected points per jornada = shrunk pts/match (K=8, 2025-26) × P(start), from `ffcore/score.py` — the same scorer rivals.py uses. Injured, suspended and unavailable score zero; a doubt is halved._

_Generated 2026-08-15 16:58 UTC._
