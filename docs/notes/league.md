# ffcore/league.py — design notes

The long-form "why" behind the rules in `ffcore/league.py`. The source keeps
a one-line pointer to each section here; this is where the dates, rejected
alternatives and concrete numbers live. Nothing here is optional context —
each note exists because the shortcut it rules out was tried, or because a
real report was wrong before the rule existed.

## cash estimation model

The app publishes no balances, so `League` reconstructs them:

    cash = anchor_balance - buys_since_anchor + sales_since_anchor

An anchor is a balance somebody observed: the app's own `teamMoney` on this
sweep (for you only) or a line typed into `inputs/cash.txt`. Confidence is
`known` (anchored on an observed balance, exact ledger arithmetic since),
`estimated` (anchored on the starting budget), or `unknown` (no budget
configured, or no ledger coverage — value is `None`). Treat "estimated" as a
ceiling, not a balance: it ignores app income and any deal missing from the
feed.

## why budget-minus-roster-value was wrong

`budget - (market value of the initial roster)` was tried and was WRONG: it
charged every manager for players they were given for free by the draft, and
put all four rivals tens of millions under water. Nothing computes it any
more; the method that did was carried, uncalled, until 2026-08-19. The
draft hands out a randomised roster at no cost plus a separate cash balance,
so the anchor has to be the whole starting budget, not budget-minus-roster.

## overdrawn balances are a real state, not an error

A balance can be NEGATIVE, and that is a position, not an error. The app
lets a manager commit past their balance while the transfer window is open;
the actual constraint is being solvent when the jornada locks. So an
overdrawn manager gets the negative number, the arithmetic that produced it
(in `Cash.basis`), and a `max_bid` of zero — they must sell before they can
buy again. This used to be reported as "unknown" plus a warning, which threw
away a real number and made every rival's ceiling unreadable. Only a missing
budget produces "unknown" — the one state that means "could outspend you"
and suppresses every bid ceiling downstream.

## rival cash income measurement (`flat_income`)

ONE ACCOUNT STATES A BALANCE (yours) and every other is a replay, so the
only place the app's payouts can be MEASURED is your own row: whatever your
balance holds that the budget and the transfer feed do not explain is what
the app has paid you since the season began. Measured 2026-08-19: 1.27M,
against the 0.80M that eight days of the configured daily bonus came to —
so every rival's ceiling was half a million light, and a rival who can
outbid you by 0.4M is exactly the kind of thing worth knowing. It is
credited to rivals on the assumption the app pays everyone alike, which is
what the one clean observation says: a day with no deal in it moved the
balance by exactly +100,000. If it ever turns out to pay by position or by
points, this is the number that will stop reconciling. Never negative — a
balance below what the ledger explains means the ledger has missed a
purchase, and spreading that across four rivals as a negative award would
turn one bad row into five.

## the daily allowance backfill (`allowance`)

THE ANCHOR IS THE START, WHATEVER LABELLED IT. This used to apply only to
ESTIMATED balances, on the reasoning that an observed balance already
contains every bonus paid — true of the moment it was read, false of every
day after. The app's own reading is seconds old so it is owed ~0 either way;
a line typed into `cash.txt` four days ago is owed four days, and without
crediting that it came back 0.40M light while still calling itself "known".
No anchor pays `(0, 0)` rather than a guess, and an anchor in the future
pays nothing — a clock a little out is not a windfall.

## exact-value join as a last resort (`_by_exact_value`)

EXACT, no tolerance, on purpose: the join is only trustworthy because
futbolfantasy publishes the same euro figure the app does, and a euro of
slack turns it into a fuzzy match over a dense number line. Searched ACROSS
ALL OF HISTORY, not just the newest snapshot — api_teams is swept once a day
and market.csv every run, so hours later the app's figure is one the market
has already moved on from; searching only the latest reading made this join
work for half an hour and then quietly stop. Uniqueness is per PLAYER, not
per row: the same player at the same value in thirty snapshots is one
candidate; two different players who have each been worth 500 at some point
are two, and two is no answer.

## the app_ids table — a one-way, additive cache

`app_ids_known()` / `_app_ids_of()`: THE TABLE ALREADY KNEW. `players.csv`
is the merged answer of every join ever made ("it merges, it does not
rebuild"), so a player the app named once in a market row is nameable
forever afterwards, while the ownership join re-derives him from today's
row alone and can fail. THE CYCLE IS DELIBERATE AND ONE-WAY: crosswalk.py
builds `players.csv` using `api_key`, and `api_key` now reads
`players.csv` back — but the file on disk is the PREVIOUS run's answer, and
the map only ever ADDS to a join that would otherwise have failed. No file
is not an error: on a cold start this is `{}` and every caller behaves
exactly as it did before the table existed.

## ownership from the app API (`owner_from_api`)

Ownership WITHOUT a replay: `replay()` reconstructs ownership from a
starting roster plus every typed transaction, so it inherits every gap in
that file; this asks the app directly, so there's no accumulated drift.
Keyed through `market.key_for`, the same resolution every other reader
uses — a key nothing else recognises is a player who quietly vanishes from
the watchlist and the board. Unjoined names come back to be printed, never
dropped: dropping one marks an owned player as a free agent, which is how
you end up bidding for somebody a rival already has. `ledger_owner` breaks
the one tie the app creates: it lists some players by surname alone
("Cardoso" with a Fabio and a Johnny in the market, "Llorente" with a Marcos
and a Diego Javier), and `key_for` correctly refuses to pick — but the
ledger identified those players at purchase time, price check and all, so
when exactly ONE candidate is already recorded against the SAME manager,
the two sources agree and there's nothing left to guess.

## price as a name-join sanity check (`_priced_like`)

THE PRICE CHECKS A GUESS, NEVER AN EXACT MATCH. `key_for` answers two
different questions with one string: sometimes the market carries that very
name, and sometimes it resolved an abbreviation to the only plausible
candidate. The first is the strongest evidence there is and money must
never override it. The second is a guess, and the app states a price on the
same row: "C. Romero" once resolved to ISAAC Romero, at 6.15M against the
43.24M the app had just quoted for him. True whenever either side is silent
— an absent number disproves nothing.

## `app_fielded` — the app's own lineup

/v1/competition/1/teams/{team}/lineup/week/{n} returns the formation
actually set — found 2026-08-19, after a season of believing it did not
exist because every guess had been made under the LEAGUE path. It replaces
`inputs/lineup.txt`, a hand-ticked checklist that went one short whenever a
fielded player was sold. ALL OR NOTHING: one unresolved row and the whole
result is `[]`, so the caller falls back to the marks — a man who fails to
resolve must not silently drop out of "what you are fielding" and come back
as "put him on" (advice to make a change you've already made).

## `api_key` — the resolution order, and why

AN ID FIRST, ALWAYS — reordered 2026-08-21. This used to run two name joins
ahead of the app's own id, to protect against a stale crosswalk mapping
(app_id 2614 was once written onto the wrong Romero by a bad name join).
That protection is `Crosswalk.merge()`'s job now — a corrected join
displaces a stale id off whoever wrongly holds it, on every rebuild — and
the id itself involves no derivation: it is a raw fact off the app's row.

The five-step chain:
1. the app's player id, in `app_ids` (from `players.csv`, built BY this
   function — absent on a cold start, must stay additive).
2. `market.key_for` on the app's nickname.
3. `market.key_for` again on the app's FULL name — tried second because the
   nickname is the better single guess: of 76 owned players, twelve join
   ONLY on it, their full name being a birth name nothing else uses
   ("Pepelu" is "José Luis García Vayá").
4. the ledger breaking a tie when the app gives a surname the market has
   two of, and exactly one candidate is already recorded against THIS
   manager.
5. an EXACT market value, searched across all of history.

TODO: steps 1-3 still duplicate what `Crosswalk.resolve()` now does. Not
migrated because `_priced_like` needs to apply differently per step
(unconditional trust on the id, price-validated on the two name guesses)
and `resolve()`'s single return value doesn't say which step answered —
merging cleanly needs `resolve()` to expose that, or this function to
accept an `xw: Crosswalk` and call the id/name pieces separately. Left
alone rather than force a fit.

None means unresolved, and unresolved must stay visible: a dropped row
reads as an owned player turned free agent, or a rival's clause-holder
nobody can bid for.

## ledger reconstruction from the app's activity feed (`ledger_from_api`)

The ledger used to be typed by hand after every deal, which made it the one
input that could silently fall behind — on 2026-08-17 it was three days
behind, which is what made a report offer a 63.29M budget against a real
23.60M. A feed cannot forget. ONE THING THE FEED CANNOT SAY: who the
counterparty was. Every row names `user1Id` and nobody else, and a
manager-to-manager transfer does NOT appear as a paired buy and sell —
checked against all 57 rows, no two share a player and a moment. So a buy
is written as coming from the pool and a sale as going to it — right for
ownership and for every premium, wrong only for the narrative of who dealt
with whom. A row whose player or manager cannot be named is DROPPED rather
than written blank: a ledger row with no player joins to no market value,
and would quietly distort the premium medians built on it.

## `identify()` — counterparty pruning for a ledger row (issue #26)

The counterparty is evidence about who a player is. A sale names someone
that manager was holding; a purchase from the market names someone nobody
held. Either prunes a candidate list that the name alone leaves ambiguous —
the same manual step the ledger's own hand-typed notes used to record
("price confirms Fabio not Johnny"). Three prunes, applied to
`Market.candidates()`'s own list rather than replacing it (an exact name is
never second-guessed): (1) sold by a manager -> he was in that manager's
squad at the time; (2) bought from the market -> nobody in the league held
him; (3) priced -> within a factor of `PLAUSIBLE` of his value at the time
— two players who share a surname rarely share a price bracket. An
identifier off the row (the app's own id, via the crosswalk) is tried first
and is not a guess at all — everything else here is string matching with a
counterparty and a price to prune it.

## `_roster_key` — migrating onto `Crosswalk.resolve()`

A thin wrapper over `Crosswalk.resolve()` — the first of six call-site
resolvers migrated onto the one join function, per the 2026-08-21 handoff.
`read_rosters()` folds "alvaro garcia (Rayo)" into "alvaro garcia@rayo" for
a shared name, split back apart here so the club reaches `resolve()`'s
`hint_club`. Falls back to `norm(raw)` only when `resolve()` has nothing —
no crosswalk given, or the name has never been seen anywhere, or is still
ambiguous — which `resolve()` itself cannot do without a crosswalk to fall
back to.

## `replay()` and `League.__init__` — the app overrules the ledger

`replay()` accumulates typed transactions over a starting roster, so it
inherits every row nobody typed. The API states ownership outright, so
`League.__init__` prefers it — the replay still runs (it produces prices
and premiums), but its ownership is superseded whenever the API answers. An
EMPTY feed changes nothing: a token expiring mid-season must degrade to the
ledger, never announce that nobody owns anybody. No market, no override
either — the join needs `Market.key_for` to produce keys the rest of the
repo recognises; without one the app's own spelling would become the key
and the squad would silently stop matching the checklist, the watchlist and
the board (real case: a squad holding "a ferllo" while the checklist,
correctly, held "alvaro fernandez", each file complaining the other was
wrong).

## `_estimate_cash` — anchor + arithmetic, shown in full

The app's own balance wins over anything typed — it is still an OBSERVED
balance, just observed by machine, to the euro, on every sweep, so it can
never be the thing that goes stale (on 2026-08-17 a typed anchor was two
days old and the report offered 63.29M against a real 23.60M). ONE BUDGET
for everyone: a per-manager override sat in `league.ini` for a year and
never held a value — a knob never turned buys nothing and can still go
wrong, so it was removed. THE WHOLE MOMENT, parsed as UTC, not the day: the
API balance is the app's answer as of the sweep, so every deal up to then
is already inside it — truncating to a date makes a same-day deal look
later than the observation and subtracts it from a number that already
counted it (23.60M reported as 41.92M, a real case). The daily allowance is
credited from the anchor's age, not its label — see the allowance note
above. Every term of the final arithmetic is kept in `Cash.basis` so a
balance that looks wrong can be checked against the ledger without
re-deriving it, and an overdrawn manager's position can be sized at a
glance.
