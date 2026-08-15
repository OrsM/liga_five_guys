# Fantasy report — 2026-08-15T1920Z

**Locks in 19h** (next kickoff) · squad 171.50M · cash 63.29M · total 234.79M

## 1. Am I fielding the right eleven?

**Your XI: 4-5-1 · ≈35 pts expected next jornada** (uncalibrated — see the methodology link at the end)

| | Marked XI | pts/m | FF | AF | xPts/j |
|---|---|--:|--:|--:|--:|
| POR | Ionut Radu | 6.6 | 90% | 67% | 5.9 |
| DEF | Carl Starfelt | 5.1 | 60% | 67% | 3.1 |
| DEF | Robin Le Normand | 4.4 | 60% | 50% | 2.6 |
| DEF | Igor Zubeldia | 3.7 | 60% | — | 2.2 |
| DEF | Omar El Hilali | 3.0 | 80% | — | 2.4 |
| MED | Iñigo Ruiz de Galarreta | 4.5 | 60% | — | 2.7 |
| MED | Dani Lorenzo | ~2.8 | 90% | 100% | 2.6 |
| MED | Pepelu | 4.3 | 70% | 67% | 3.0 |
| MED | Jon Moncayola | 4.4 | 90% | — | 3.9 |
| MED | Lucien Agoume | 3.9 | 100% | titular | 3.9 |
| DEL | Iñigo Vicente | ~3.0 | 90% | 100% | 2.7 |

_**pts/m** is points per match from last season's totals, shrunk toward the average for a short record — a record, not a fixture-aware forecast: it does not know who the opponent is. `~` means no top-flight record at all, so the baseline is assumed. **FF** is futbolfantasy's published start percentage, **AF** is analiticafantasy's; they are separate columns because neither has been checked against a played jornada yet, so there is no weight to blend them by. **xPts/j** = pts/m × FF, and uses FF only. `⚠` on a name means question 2 has something on him._

**The model would score ≈35 — 0.3 pts/j more.** Its shape is 4-5-1.

| Bench this | For this | Worth |
|---|---|--:|
| Dani Lorenzo (2.6) | Ruben Garcia (2.9) | +0.3 |

_Swaps are same-position only: a cross-slot difference is a change of formation, not a substitution. Your own marks are the row above — this table is advice._

## 2. Is anyone injured, suspended, or doubtful?

**Nobody in your squad is flagged.** All 15 players with an entry on their team page read as available.

_Listed with no flag (15): Alvaro Fernandez, Beñat Turrientes, Carl Starfelt, Dani Lorenzo, Igor Zubeldia, Ionut Radu, Iñigo Ruiz de Galarreta, Iñigo Vicente, Jon Moncayola, Lucien Agoume, Omar El Hilali, Pepelu, Robin Le Normand, Ruben Garcia, Simon Eriksson._

_Read from the 'Estado físico', 'Sancionados' and 'No disponibles' blocks of each team page. A knock the site still lists as available (`Tocado`) is folded into doubt._

## 3. Is everyone expected to start?

**The two sources disagree** — one of them has him in the eleven and the other does not. Neither has a track record here yet, so this is a prompt to open the app, not a verdict:

| | Player | The split |
|---|---|---|
| XI | Robin Le Normand | futbolfantasy 60%, analitica 50% |
| bench | Beñat Turrientes | futbolfantasy 60%, analitica 33% |

_Both figures are editorial reads refreshed a few times a day, not live probabilities. `~` means listed with no figure (assumed 60%), `!` not on the page at all (assumed 15%). Threshold is `min_start` in `inputs/league.ini`._

## 4. Anything to do in the market?

_No slate pasted, so there is nothing you can bid on today that this report knows about. Paste today's market screenshot into the `seen` input to price it. Everyone unowned is ranked in `reports/watchlist.md`._

---

## Warnings

- **Only 1 delantero** — one knock and you can't field a legal XI.
- **1 unmodelled** (Iñigo Vicente) — no LaLiga record, so they carry an assumed baseline, not an earned one.

_Compare squad value with the app; a mismatch means a name matched the wrong player. Roster read from the ledger._

## Not in your XI (as you have marked them)

**Gap** is what the XI loses per jornada if he has to play, after re-picking the formation. **€/pt** is value per expected point: the sell shortlist, worst first.

| Player | Pos | Value | xPts/j | Gap | €/pt | Why |
|---|---|--:|--:|--:|--:|---|
| Alvaro Fernandez | por | 4.73M | 0.9 | -5.1 | 5.42M | 3rd POR — only 1 can ever play |
| Ruben Garcia | med | 13.75M | 2.9 | +0.0 | 4.77M | rising — sell into strength |
| Beñat Turrientes | med | 7.06M | 2.2 | -0.5 | 3.28M | 7th MED — only 5 can ever play |
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

_620 players tracked, 527 with a probable-XI reading._

_xPts/j — expected points per jornada = shrunk pts/match (K=8, 2025-26) × P(start), from `ffcore/score.py` — the same scorer rivals.py uses. Injured, suspended and unavailable score zero; a doubt is halved._

_Generated 2026-08-15 19:44 UTC._
