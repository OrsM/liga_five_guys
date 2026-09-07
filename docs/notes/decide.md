# decide.py — design history and rejected alternatives

Background for `src/decide.py`. The code carries a short pointer comment
(`# Why: docs/notes/decide.md#<anchor>`) at each spot below; this file holds
the full story — who found what, when, what was tried and rejected, and the
real numbers behind each decision. Read this before changing any of the
invariants the code states tersely.

## trial counts (SCREEN_TRIALS / FINAL_TRIALS)

Screening runs at a fraction of FINAL_TRIALS. Common random numbers mean the
*ranking* settles long before the *levels* do, so screening cheaply and
re-running only the survivors at full count buys an order of magnitude of
speed for free.

**Is 3000 false precision?** Checked 2026-08-31 (Miguel asked directly). Ran
the real board at N in {100, 250, 500, 1000, 1500, 3000}, same seed every
time. The top move (a *paired* comparison — with the move vs. without it,
same simulated seasons) was identical at every N from 100 to 3000: the
ranking claim holds all the way down. `p_win`/`expected_finish` (*levels*,
not paired differences) did not — they swung 0.1930–0.2600 across the same
run, a 7-point spread on a number the report prints as one figure. Cutting
FINAL_TRIALS would speed up an already-cheap stage (7.0s at 3000, 2.9s at
1000 — most of the difference is trial-proportional, the rest is fixed
overhead that doesn't shrink below ~1000) in exchange for more noise on
exactly the number `sim.trailing()` thresholds at 0.5 — enough to flip
whether that mode fires on a genuinely borderline day. Kept at 3000.

## KEEP_RELIABLE_MIN — reliable candidates always reach the full pass

Checked against the real transaction ledger 2026-08-29: 108 recorded
transactions, all 108 "from the app" — zero manager-to-manager sales, ever.
Re-checked 2026-08-31 at 119 rows, this time *proven* rather than read off
the ledger's own from/to columns (which structurally cannot show a
counterparty — `ledger.py` names one manager per row by construction, so
"0 manager-to-manager" read off those columns would just be reading the
writer's own lossiness back as a finding). The real check replays ownership
from `inputs/rosters_initial.txt` forward over every `api_activity` row: 119
deals, zero buys of a player somebody already held, zero sells by anybody
but the holder, and the two sell→buy pairs on the same player are 4 and 10
days apart (sold to the app, later re-bought — not a transfer).

Same for all four rivals — a real negative result, which is why there's no
per-rival version of this constant. Attributing every `marketPlayerTeam` row
to whoever owned that player at that moment: Albert Laporta 20 players
listed, BurtonGM89 19, SusoGattuso 18, Magic Mike 333 zero, ever. 44–55% of
those listings did eventually leave the squad, but every one left *to the
app* — so the rate that matters here (a listing becoming a sale to another
manager) is 0/57 pooled and 0 per rival individually. The three who list are
within binomial noise of each other (±11pp at n≈20); the fourth has never
given the question a data point. So the prior stays league-wide. What *is*
differentiated per rival is how fast each can raise money — see
`rival_tempo()`/`days_to_afford()`.

A "listed" candidate (a rival's own sale, who can simply not sell) screens
on the same raw gain as a guaranteed free/clause one, and on real data
listed candidates filled 10 of KEEP's 12 slots — a free-agent move outside
the raw top 12 never reached the full FINAL_TRIALS pass at all, not even to
show a real band in the table, because listed candidates that will likely
never happen crowded it out of the *sample*, not just the final
recommendation. `_top_up()` tops up `keep` with the best-screened reliable
candidates not already in it, on top of the natural top-KEEP, never
displacing anything.

## KEEP_VALUE_MIN — efficient-but-modest candidates also reach the full pass

Same mechanism as KEEP_RELIABLE_MIN, different axis. `screened` is sorted by
raw gain, so `top = screened[:KEEP]` structurally cannot contain a candidate
that gains little but costs almost nothing: on a day the top-12 are all
€5-20M moves for 0.1-0.3 places, a €200k move worth 0.05 places never
reaches the FINAL_TRIALS pass — not ranked low, not scored at all. Tops up
`top` with the best-screened-by-efficiency candidates not already in it,
same non-destructive shape. Efficiency = the cheap screen's own `d`
(expected-position delta) per net euro — a proxy for the real `value`
FINAL_TRIALS computes later.

## Universe fields

- `value` vs `price`: `value` is what the app says he's worth (everyone);
  `price` is what it costs *me* (a clause runs a median 1.52x market value —
  see `burn()`).
- `market_exp`: expected points for every player the *market* prices, not
  only the 89 in the simulation. Before this existed, `expected()` returning
  0.0 for a player it was never given was indistinguishable from him being
  worthless — it made Lamine Yamal and Vinicius Junior score zero, and
  produced a finding that only four players in the whole league could
  improve the eleven.
- `start`: P(he starts) as one recalibrated number (futbolfantasy blended
  with analiticafantasy via `ffcore.startprob`) — printing "80/100" made the
  reader do the weighting; the weighting is fitted, so it's done once.
- `route`: how you'd get each player — "free" (app dealing a free agent),
  "listed" (owner's own sale — see the 0/119 finding above), "clause" (only
  route is the buyout). Only "clause" is a raid in the sense that pays the
  owner. Before 2026-08-22 every acquirable player was one undifferentiated
  "market" value; free/listed split out once it was measured that most of a
  day's "market" rows are a rival's own listing, not a free agent (28 of 41
  the day the feed's own seller field was first read).
- `tempo`: each manager's realised transaction behaviour (`rival_tempo()`) —
  the per-rival read that the league-wide "listed never converts" prior
  (KEEP_RELIABLE_MIN) doesn't support, but that money does.

## current_xi() — one computation, seven old copies

Found duplicated across `xi_note()`, `fielded_shape()`, `ladder_rows()`,
`wait_routes()`, `_xi_total()`, `_shape_now()` (sim.py) and `candidates()`
(this module) — each with its own `exp = forecaster.expected(choosable(u));
xi = best_xi(squad, exp)` pair. All deterministic (no trial-to-trial drift
risk), but still seven implementations of one fact kept in sync by hand.
`exp` is the same dict regardless of `who` — only the squad it's read
against differs.

## xi_bar() — why the bar is flat across all four slots

Value-over-replacement theory says the bar ought to be position-specific —
the worst starting defender isn't the worst starting forward, and on the
real board those bars are genuinely far apart (measured 2026-08-31: POR
5.82, DEF 2.69, MED 2.54, DEL 3.62). A per-position bar would still be
*wrong* here: you can change your layout. A candidate below his own slot's
bar can still enter the eleven by pushing that slot's count up and some
other slot's down — he helps the moment he beats the worst starter
*anywhere*, which is exactly the flat minimum. The self-test carries the
exact shape: a 1-5-4-1 with a weak fifth defender, where a midfielder at 2.0
is below MED's own replacement level of 4.0 and still gains a point by
reshaping to 1-4-5-1 and dropping that defender.

So the flat bar is the loosest *sound* screen, erring the safe way: it lets
through candidates who can't actually help (4 of 19 real targets on
2026-08-31, two of them keepers, since you only field one), and the
simulation then prices those at roughly nothing (that day's keeper clause:
-0.54 season points) — a few wasted screening slots, versus the other
error, a lost move.

## RETIRED 2026-09-06 (`bbf1f2d`, Phase 1 of the aggressive-simplify branch)

The five sections this replaces (`_safe_to_sell()`, `candidates()`'s
funding-chain notes, `rival_tempo()`, `days_to_afford()`, `contest()`,
`best_swap_for()`) documented multi-sale funding chains and clause-race
timing — real, working mechanisms, but the source of BOTH catastrophic
squad-legality bugs this repo has had (Ali Houary's -1282, Alvaro
Mantilla's -1286: a chain could clear every position's own SLOT_MIN
individually while totalling one short of a real formation). Miguel,
after the second occurrence in one week: "getting too complex... away
from 'the book' approach" — asked for an audit sorting every mechanism
into core vs. bolt-on, ranked by bug cost. Funding chains and
clause-race timing were the two costliest. Cut rather than re-patched a
third time: `candidates()` now funds a move by cash alone, or by selling
EXACTLY ONE spare player — never a chain. A genuinely 2-sale-only move
simply stops appearing; that's the honest trade for never generating
this bug class again. `_fieldable()` (below) replaced `_safe_to_sell()`'s
bounds heuristic with the actual existence check that closes the bug
class for good, not a smarter bound. `rival_tempo()`/`days_to_afford()`/
`contest()` (per-rival cash-race timing) and `best_swap_for()` (a KEEP
row's "vs X" note) are gone outright, not replaced — real, working
code, cut on the same "the book" judgment, not a bug of their own.

## _fieldable() — the one squad-legality check

Replaces `_safe_to_sell()`'s bounds heuristic (per-position floor +
total count) with the actual question: does SOME real formation
(`ffcore.score.formations()`'s 7 tuples) fit this squad's shape? Counts
only — no player identities, no simulation, not even `best_xi()` — but
counts against REAL formations, which a bounds check cannot: a squad can
clear every position's own SLOT_MIN individually, and total XI_SIZE, and
still have no real formation that fits it (two goalkeepers left in an
exactly-11-player squad — no formation ever fields two). That exact
shape produced both catastrophic bugs named above, through two
DIFFERENT bounds checks (`illegal_squads()` for a rival's squad,
`_safe_to_sell()` for a hypothetical sale chain) that each added a
smarter bound without closing the actual failure class. The lesson,
now load-bearing: whenever checking "is this squad shape legal" ANYWHERE
in this codebase, call `_fieldable()` (or `best_xi()`/`formations()`
directly), never re-derive a bounds approximation of it.

## route_kind() — the one place ownership is classified

`"mine" | "free" | "raid" | "listed"`, purely off `u.owner`/`u.route` —
no simulation. Before this (2026-09-06), the same three-line boolean was
re-derived by hand at four call sites (`candidates()`, `best_swap_for()`,
two separate loops in `sim.ladder_rows()`), plus `not u.owner.get(k)`
rewritten inline three more times for "free agent." That's exactly how a
rival-owned, non-clause target kept leaking back into the report after
three earlier targeted fixes each patched one site and missed the
others (2026-09-05's PASS-section bug was the third). Miguel's own
framing, once he saw the pattern: this was never a simulated fact, it's
a report FILTER, fully decided by two fields already on `Universe`, with
no computation in between — "I always saw it as a report filter."
Consolidating it surfaced a fifth, previously unreported instance:
`best_swap_for()`'s own upgrade search had never excluded listed targets
at all (moot now — `best_swap_for()` is retired, above).

## candidates() — listed targets are never proposed

0 of 119 real transactions in this league have ever been manager-to-
manager (re-derived and proven 2026-08-31 by replaying ownership forward
over the real ledger, not read off ledger columns that name only one
manager per row by construction). A "listed" target — a rival's own
choice to sell — competes on paper like a real option but essentially
never converts, so `candidates()` filters it out via `route_kind()`
before it ever becomes an `Action`, not demoted or flagged downstream.
Third time this exact complaint recurred (Miguel: "I don't think I'm
making myself clear... the report does not include it like that") after
two earlier fixes had only reorganized/relabeled these rows — asked
directly (rather than guessing a fourth variant) whether to summarize,
relocate, or drop entirely; he chose drop, no mention anywhere.

## load() — memoized for the process

Pure w.r.t. the tidy store (no argument changes the result). `run.py`'s
single-interpreter design meant `methodology`'s stage and `sim`'s stage
each paid for their own fresh call — `methodology`'s own `_fc()` helper
called it twice by itself. Measured 2.4s per call; caching cut it from 3
calls to 1 on a real report. Same shape as `wait_routes`/`market_model`
being hoisted to one call in `sim.py` — a store read, not a fact that
could change mid-run, so nothing needs invalidating within one process.

## rank() — screening, top-up, and `value`

**Common random numbers matter measurably**: recalibrating P(start) on
2026-08-18 moved a row's P(win) by 48 points and its paired figures by six.

**`extra` rides along in the same pass** rather than running a second one,
because the draw doesn't depend on the squad — at 3000 trials the pass costs
~1.2s of drawing plus 0.03s per squad scored (measured 2026-08-24), so a
second pass would pay the 1.2s again for nothing.

**A `key` already answered by a real BUY row is dropped from `extra`** — its
own `pts_lo`/`pts_hi` answer better, off the squad the victim's response
leaves behind. Buy side only, deliberately — checking the sell side too once
dropped a held player's band for an unrelated reason: found 2026-08-25 when
Jon Moncayola's OUT row went blank because he happened to fund the
top-ranked clause move.

**`value` (season points per million actually paid) is not the old λ.** λ
(retired 2026-08-17) measured points-of-XI-index per million against *your
current eleven* off a ladder built from the whole unowned pool — the
baseline moved (the same player was worth a different λ on consecutive days
for reasons that had nothing to do with him). `d_pts` here comes from
`rank()`'s own paired Monte Carlo — the same simulated seasons, with the
move and without it — which is robust to a changing model in a way λ's
ladder never was.

**Only defined for a genuine spend (net > 0).** A sale that raises more than
it costs is free money plus points — no rate needed to justify it.

**Already points over position-replacement level, computed not assumed** —
asked again 2026-08-31 whether a separate `value_vor` was needed; no,
because `d_pts` is a paired marginal where the "with" side re-picks
`best_xi()` over every legal shape, so a signing is already scored against
exactly the man he displaces, in the formation you'd actually field once he
arrives. Measured in the self-test: two candidates on the same expected
points and the same price, one into a thin slot and one into a deep one,
come out 3.2x apart in `d_pts` and so in `value`. A static per-position
baseline would be strictly worse, not merely redundant — it can't see the
reshape (see xi_bar()'s own note) and reintroduces a rate with its own
baseline to drift, the exact thing both module docstrings retired "value
over replacement" for.

## rounds_left() — a jornada with some scores in still counts

A jornada with every score in is finished and isn't simulated. One with
*some* scores in (the August case) pays twice if simulated whole: it's still
ahead, so the simulator plays it, while the app has already banked the
played matches into the carried total. On the day this was found, four of
ten J1 matches were in, and it was handing BurtonGM89 20.3 phantom points a
round against Miguel's 7.8. So the round stays, and the clubs inside it that
are done drop out — their real points are already carried.

Still doesn't model: the eleven for a round in progress is *already locked*,
and the simulator re-picks it from whoever's left — flattering everybody by
letting them field a team they can no longer field, for one round out of
thirty-eight.

## next_then_rest() / apply_fixtures()

This week's status flag (a suspension, a knock) is real news about the one
game it was published for. Handing that same reading to every remaining
jornada of the season — what this repo did before — was reading "he plays
Sunday" as also "he plays in March." "First remaining jornada" is *per
player*, not one global jornada, since a partial round mid-sweep drops a
player from `rem[0]` once his own club has already played it.

`apply_fixtures()` reprices the points half against each jornada's *real*
opponent (the schedule is published for the whole season, so pricing
jornada 20 off jornada 3's opponent isn't a modelling limit, it's not having
asked).

## phantom_fill() — why a short squad gets a phantom, and why it's an average

Found 2026-09-01 (Miguel: "the forecast for Albert is absolutely
unsustainable... that's not possible unless he never again connects to the
app"). `best_xi()` needs SLOT_MIN of every position to fill any of the 7
real formations — a squad short in even one returns `[]`, scoring zero every
remaining jornada with zero variance. That's not a forecast of a weak
season, it's the simulation being structurally unable to score him at all.
Real managers fix a squad this broken; assuming he never will would be a
much stronger claim than assuming any other rival makes some discretionary
improvement — which this repo's own "rivals never transfer" caveat already
refuses to assume. Filling the one gap that keeps a manager from fielding a
team at all is a different, safer claim: every manager clears that bar to
participate, or the game isn't being played.

**The average, not a specific player or an invented number.** A real
player's own key would need to disappear from the BUY list while
"borrowed," and would drift day to day with whichever specific player
happens cheapest — noise unrelated to the real uncertainty being priced.
Averaged straight off the same real per-jornada data (points and P(start))
every other player at that position already carries.

**No `matches` entry, deliberately** — Bootstrap's own persistent-error walk
is only computed for a key present in `matches`, the same "no evidence, no
widening" rule already applied to a brand-new player with zero real
history. Keyed `__phantom_<manager>_<slot>_<n>`, a form no real player id
can take, so it can't be bought or mistaken for a real man, and drops out of
`illegal_squads()` once it makes the squad legal again.

Confirmed live: Albert's standings row moved from a flat 32-32 band (100%
P(above him)) to a real 1,228 (867-1,631) band, 72%.

## load() — misc join notes

- Ownership is `League`'s, not re-derived — a second, weaker join here would
  mean five rival players nobody can be recognized as owning.
- `pt_to_key` (the app's ownership-record id → this repo's key) is built
  once in the same loop that already resolves every `api_teams` row — a
  second resolution of the same rows for one more field is the mistake
  `ffcore.league.owner_from_api` was written to stop.
- A clause you can't pay isn't a price at all — the app refuses the
  transaction outright, so it's excluded rather than ranked low.
- Cash reads the same estimator `league.md` uses (accrues the daily
  allowance since the anchor) rather than a second independent read of the
  raw balance — two copies of one fact is how a number gets corrected in
  one place and not the other; this exact split once left the headline
  quoting a three-day-old balance from a feed everything else had refused.
