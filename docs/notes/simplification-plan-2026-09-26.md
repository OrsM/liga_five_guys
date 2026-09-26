# Simplification plan — written 2026-09-26

## Aim

Not a function-count or line-count target — those were tried twice before
this session (docs since deleted; one shrank without making the code
easier to change, one grew it by 1,696 lines) and rejected for the same
reason both times: a bottom-up "what looks duplicated" search can't tell
a wrapper from a tool, and gets satisfied by cramming logic into fewer,
more tangled functions rather than actually simplifying anything.

This plan instead covers the full grouping of jobs the codebase does (big
pipeline stages -> fundamental contracts -> which functions implement
each), built this session by reading real code, not by pattern-matching
on shape. Every area is listed below: some need action, most were checked
and are correctly left alone, and that's stated explicitly rather than
left ambiguous.

## Area 1 — identity resolution happens late and separately, at least ten times over

**Status: done** (e34ec2d, 3939dbd). Chose neither (a) nor (b): the real
defect was one table resolved by three different ladders, not repeated
work, so lineup/starters/perjornada rows go through one
`Crosswalk.key_of(row)` at every consumer (Scorer's private ladder
deleted). `player(name=)` gained a unique-name rung (the old one never
matched on real data). `attach_market()` now actually has callers; the
threaded `market=`/`index=` params are gone. `ledger_owner` stays per
call: the owner map is the replay's inside League and the app's in
`decide.load()`, so it is not a run-constant. Also fixed `decide.load()`,
broken since b46c78e by a `load` name clash.

**Revised a second time, 2026-09-26, after being told the first revision
was still under-ambitious: it accepted "needs decision-time context" as a
hard wall for items 8-9 instead of asking whether that wall is a fact
about the world or an accident of how `League`/`Universe` get built up.
Pushed one level further below, with the actual method bodies read (not
just call sites), the wall mostly dissolves. What follows replaces the
"Proposed approach" and "Success test" from the prior revision; the
inventory of ten call sites above it still stands and is not repeated.**

### The one true contract

Every one of the ten call sites is doing the same job: **given whatever
identifying signal a single upstream row happens to carry — a typed
foreign key (ff_slug/af_slug/app_id/understat_id), a free-text name, a
club/team, a price, an owning-manager handle — return the one canonical
player key it refers to, or `None` if the signal on hand doesn't narrow
it to exactly one.** That's it. There is no second job hiding in here.
The four current call shapes (`xw.player(**id_kwargs)`, `xw.resolve(name,
hints..., market=)`, `xw.resolve_api(name, handle, market, owner, index,
value, full, app_id)`, `market.key_for(name, team, value)`) are not four
different jobs — they're the same disambiguation ladder (id lookup ->
market name/club/price match -> owner narrowing -> price-only narrowing
-> name fallback) implemented three-and-a-half times, each missing rungs
the others have, for no reason connected to what the caller actually
needs:

- `Crosswalk.player(**kwargs)` (`ffcore/crosswalk.py:121`) is pure typed-
  index lookup (ff_slug/af_slug/app_id/understat_id, then an exact
  norm-name match) — no market, no ambiguity handling. It's the bottom
  rung of the ladder, not a different ladder.
- `Crosswalk.resolve()` (`crosswalk.py:140`) is the same bottom rung
  (`self.player(app_id=..., ff_slug=..., af_slug=...)`) plus a market
  rung (`market.key_for(candidate, team=hint_club, value=hint_price)`
  over `raw` and `hint_full`) plus the same name fallback `player()`
  already has. It has club-narrowing. It has no owner-narrowing and no
  price-only value-index fallback.
- `Crosswalk.resolve_api()` (`crosswalk.py:167`) is the same bottom rung
  (id lookup, but only `app_id`, price-validated) plus a market rung
  (`market.key_for(raw, value=market_value)` then again on `full` —
  **note: never passes `team=`**, so it can't do the one piece of
  narrowing `resolve()` can) plus owner-narrowing
  (`market.candidates(raw)` filtered by `ledger_owner.get(c) == handle`)
  plus a price-only value-index fallback (`_value_index`) plus the same
  name fallback `resolve()`/`player()` have (actually it doesn't even
  have that last rung — reread: `resolve_api` has no final `player(name=
  ...)` fallback at all. If nothing above matches, it returns `None` even
  when the raw string is an exact, unambiguous player name already in
  the crosswalk).
- `Market.key_for()`/`Market.candidates()` (`ffcore/tidy.py:888,916`) are
  the market-only rung of the same ladder, called directly by
  `league.py`'s `_roster_key` and `slate.py` only in the `xw is None`
  branch (defensive fallback for callers without a crosswalk, e.g.
  tests) — and called *from inside* both `resolve()` and `resolve_api()`
  as their market rung. `Market` is correctly a separate object (it also
  answers unrelated questions — `at()`, `series()`, `drift()` — that have
  nothing to do with player identity), but its name-resolution methods
  are a primitive the identity resolver calls, not a fourth resolver.

None of this three-and-a-half-way split is motivated by what the *job*
needs. It's motivated by which caller happened to grow which rung first:
`decide.py` needed owner-narrowing so `resolve_api` got it and nothing
else did; `league.py`'s roster-parsing needed club-narrowing so `resolve`
got it and `resolve_api` never did. That's an accretion history, not a
design.

### Why "needs decision-time context" was the wrong stopping point

The prior revision treated items 8-9 (`decide.py`, `slate.py`) as
permanently different because `resolve_api` needs a built `League` (an
owner map and a live value index) that doesn't exist at crosswalk-build
time. **That's true, and it's irrelevant to whether the call site can be
a one-line, zero-ladder-logic call — it only bears on *when in the
pipeline* the one-line call can start returning answers, not on whether
the call itself can be uniform.**

Read `League.__init__` (`ffcore/league.py:462-520`): `self.market`,
`self.xw`, and `self.owner` (the real, final owner map, after the
`api_owner` override at line 505) are all settled, once, in one place,
before `League.load()` returns. `decide.py: load()` (`decide.py:295`)
immediately does `xw = lg.xw or Crosswalk()` and `index =
latest_only(lg.market.rows)` — it is reconstructing, per call to
`load()`, exactly the three things `League` just finished building. The
owner map and the value index are not "decision-time, row-varying"
context; they are **run-constants, fixed the moment `League` exists** —
exactly as fixed as `xw` itself already is, which every caller already
treats as an ambient object rather than something threaded through every
call. There is nothing about market value or ledger ownership that is
learned *during* the per-team loop; it's known before the loop starts.
The "needs decision-time context" framing conflated *late in the
pipeline* with *varies per row* — only the row's own fields (name, price,
manager handle) vary per row; market/owner do not.

The fix: let `Crosswalk` hold that context once, the same way it already
holds `self.players`/`self.clubs`, instead of every caller re-passing it:

```python
class Crosswalk:
    def attach_market(self, market, owner: dict | None = None) -> None:
        self._market = market
        self._owner = owner or {}
```

Called once, right where `League.__init__` already finalizes `self.owner`
(after line 511, once `self.owner` is the real map, not the pre-API-
override draft): `if self.xw is not None: self.xw.attach_market(self.market,
self.owner)`. `slate_from_api` (`slate.py:24`, which only ever needs a
market, no owner) can do the same with `xw.attach_market(market)` at its
own entry, before its loop.

### The merged method

Fold `resolve_api` into `resolve` — one ladder, all rungs, every rung
optional and defaulting to the attached context when not passed
explicitly (so existing tests that pass `market=` directly keep working
unchanged):

```python
def resolve(self, raw="", *, hint_app_id="", hint_ff_slug="",
            hint_af_slug="", hint_club="", hint_price=None, hint_full="",
            handle="", market=None, ledger_owner=None) -> str | None:
    market = self._market if market is None else market
    ledger_owner = self._owner if ledger_owner is None else ledger_owner
    raw = (raw or "").strip()
    if raw.isdigit():
        return raw
    if hint_app_id or hint_ff_slug or hint_af_slug:
        got = self.player(app_id=hint_app_id or None,
                          ff_slug=hint_ff_slug or None,
                          af_slug=hint_af_slug or None)
        if got and _priced_like(got, "", hint_price, self._index_for(market)):
            return got
    if market is not None:
        for candidate in (raw, hint_full.strip()):
            if not candidate:
                continue
            key = market.key_for(candidate, team=hint_club, value=hint_price)
            if key and _priced_like(key, candidate, hint_price,
                                    self._index_for(market)):
                return key
        if ledger_owner and raw:
            _got, cands = market.candidates(raw)
            agreed = [c for c in cands if ledger_owner.get(c) == handle]
            if len(agreed) == 1:
                return agreed[0]
        if hint_price not in (None, ""):
            hits = set(self._value_index(market).get(_as_float(hint_price), ()))
            hits.discard("")
            if len(hits) == 1:
                return next(iter(hits))
    if raw:
        return self.player(app_name=raw) or self.player(name=raw)
    return None
```

(`_value_index`/`_priced_like` already exist at module level in
`crosswalk.py:298-332` and move onto `Crosswalk` unchanged in substance —
this isn't new logic, it's the same two helpers now reachable from one
call path instead of one-and-a-half.) `resolve_api` is deleted; every
caller becomes `xw.resolve(...)`. `player()` stays public (it's the
correct, cheap primitive for the two real single-row resolvers in
`league.py` — see below — and for tests that want to assert the raw
index directly), but no pipeline call site outside `crosswalk.py` itself
should need it once this lands — `resolve()` is a strict superset of
what `player()` gives its current callers (see per-site table).

### What changes at each of the ten call sites

| # | site | today | after |
|---|------|-------|-------|
|1|`score.py: _per_jornada_current` starters loop|`xw.player(ff_slug=slug, name=r.get("player_name"))`|`xw.resolve(r.get("player_name"), hint_ff_slug=slug)`|
|1b|same fn, perjornada loop|`pid if pid in xw.players else xw.player(name=...)`|unchanged — `pid` here is a candidate *canonical key* being tested for membership, not a foreign id to resolve; this line isn't part of the ladder and shouldn't be forced into `resolve()`|
|2|`Scorer.__init__` (both loops)|literal duplicate 4-line snippet, two places|one local helper, as already agreed — independent of this redesign|
|3|`score.py: _shots_by_jornada`|`xw.player(app_id=...)`|`xw.resolve("", hint_app_id=...)` — or keep `xw.player()` here: there's no name to fall back to in this row shape at all, so the ladder has exactly one rung and `player()` already *is* `resolve()` restricted to that rung. Not worth forcing.|
|4|understat rows (`_forward_understat_rows`)|`xw.player(understat_id=uid)`|same reasoning as #3 — no name in this row, `player()` is the right call, not a workaround|
|5|`lineupweight.py` fitting loop|`xw.player(ff_slug=slug, name=...)`|`xw.resolve(r.get("player_name"), hint_ff_slug=slug)`|
|6|`second.py: resolve_second_source`|`xw.player(af_slug=..., name=...)`|`xw.resolve(r.get("player_name"), hint_af_slug=r.get("player_slug"))`|
|7|`startprob.py: observations()`|`xw.player(ff_slug=slug, name=nm)`|`xw.resolve(nm, hint_ff_slug=slug)`|
|8|`decide.py: load()`'s `market_key()` + per-team loop|`xw.resolve_api(r["player_name"], "", lg.market, owner, index, r.get("market_value"))` / same with `r["manager"]`|`xw.resolve(r["player_name"], price=r.get("market_value"))` / `xw.resolve(r["player_name"], handle=r["manager"], price=r.get("market_value"))` — **no `lg.market`, no `owner`, no `index` argument at all**, because `xw` already carries them via `attach_market` at `League` construction|
|9|`slate.py: slate_from_api`|`xw.resolve(raw, hint_app_id=..., market=market)`|`xw.resolve(raw, hint_app_id=r.get("player_id") or "")` — `market` passed once via `attach_market` at the top of `slate_from_api`, not per row|
|10|`league.py: _roster_key`|`xw.resolve(name, hint_club=club, market=market)`|unchanged in shape — already the right call; `market=` can be dropped once `xw` is attached at `League.load()`-build time, since `_roster_key` runs during `replay()` which happens *inside* `League.__init__`, before `attach_market` has run yet (see below) — so this one keeps its explicit `market=` argument, and that's fine, it's still one line, still zero ladder logic at the call site|

For #3/#4, note the honest reasoning is the same "different row shape,
same ladder, fewer rungs available" point as before — not a second
contract. Forcing a `resolve()` call with no name and no possibility of
a name fallback would just be `player()` wearing a costume; that's the
kind of change the user is asking us NOT to make for cosmetic
uniformity. `player()` remains the right name for "I have exactly one
typed id and nothing else."

For #10: **checked again, directly, after this plan's own reconciliation
pass caught a false "structural" wall in Area 4 (§3) — this one was ALSO
stated more restrictively than the code supports.** `_roster_key(raw,
market, xw)` (`league.py:358`) calls `xw.resolve(name, hint_club=club,
market=market)` — no `handle=`/`ledger_owner=` argument, ever. It never
needed the owner map at all. `replay()` (`league.py:374`) calls
`_roster_key(n, market, xw)` using the `market`/`xw` values that are
`League.__init__`'s own constructor parameters — available on line 1 of
`__init__`, not something that needs `self.owner` to exist first. So
`self.xw.attach_market(market)` (owner omitted/`None`) CAN run at the very
top of `League.__init__`, before `replay()` is even called, and
`_roster_key` becomes `xw.resolve(name, hint_club=club)` — zero
arguments beyond the row's own fields, same bar as items 1-9. A second
`self.xw.attach_market(market, self.owner)` call after `self.owner` is
final (as already planned) then adds owner-narrowing for every later
call (`decide.py`'s, `slate.py`'s) that does need it. **Item 10 is not an
exception; it fully reaches the same bar as 1-9.**

**The actual, narrower survivor is `identify()`** (`league.py:311`, called
from inside `replay()`, not `_roster_key`), and the reason is not "the
object doesn't exist yet" (identify() runs at the same construction point
as `_roster_key` and would have the same market/xw available) — it's that
`identify()`'s disambiguation, when the crosswalk/market ladder returns
multiple candidates, resolves the tie using `owner.get(c)` (who held the
player *at that point in the replay*, not who holds him now — a value
that only exists because `replay()` is building it incrementally, one
transaction at a time) and `market.at(c, when)` (the historical price *as
of that specific transaction's own date*). Both of those are genuinely
per-row values with no run-level answer, and neither is expressible as
"the crosswalk's attached context" no matter how `Crosswalk` is
restructured — this is the one place the distinction Area 1 opened with
("late in the pipeline" vs. "varies per row") lands on the "varies per
row" side for real. `identify()` was correctly excluded from the ten-site
inventory above for this reason; restated here with the corrected reason,
since the original reason given didn't survive a direct check either.

### Behavior changes, and why each is fine

- **`resolve()` callers (slate.py, league.py `_roster_key`) gain owner-
  narrowing and price-only value-index fallback** they didn't have
  before, whenever an owner map is attached. This can only ever narrow an
  *already-ambiguous* shared name down further — it never overrides an
  unambiguous match. Strictly more disambiguation power for free. Good.
- **`resolve_api` callers (decide.py) gain club-narrowing** (`hint_club`)
  as a capability, though neither current call site has a club value to
  pass, so this is a latent no-op today, not a live behavior change.
- **`resolve_api` callers gain a genuine final name/app_name fallback**
  they never had. Today, if `decide.py`'s market-value/owner/price ladder
  all miss on a row whose raw name is nonetheless an exact, unambiguous
  match in the crosswalk (e.g. `player_id` or `app_names` equality),
  `resolve_api` returns `None` and the row is silently dropped as
  unresolved. After the merge it resolves. **This is a bug fix wearing
  the clothes of a refactor, not a risk** — there is no principled reason
  a name that would resolve via `slate.py` or `league.py`'s ladder should
  fail to resolve via `decide.py`'s ladder for the identical raw string.
  Any test currently asserting decide.py drops such a row needs its
  fixture/assertion updated to match the corrected behavior, not the
  refactor rolled back to match the old assertion.
- **The `_priced_like` sanity check now also guards `resolve()`'s market
  rung**, not just `resolve_api`'s. `_priced_like` short-circuits to
  `True` (i.e. does nothing) whenever the match came from an exact,
  non-ambiguous hit (`key == norm(raw)`), so this only engages on the
  fuzzy/ambiguous path — exactly where a price sanity check belongs. Any
  `league.py`/`slate.py` row that previously resolved via a shared-name
  fuzzy match with a market value that doesn't plausibly agree will now
  come back `None` instead of a wrong guess. That is what the check
  exists for; it should not have been decide.py-only.

### The one genuine remaining split

`league.py`'s ledger replay (`identify()` at line 311, `_roster_key` at
line 358, called from `replay()`) resolves identity **as of a specific
past instant** — a transaction's own date (`ledger_stamp(t.get("date"))`)
— using `market.at(c, when)` (a historical price lookup at that instant),
and disambiguates ownership using who held the player *at that point in
the ledger's own replay*, not who holds him now. This is the one place
where the needed signal is not a run-constant: `when` is a different
value on every single transaction row, so it cannot be attached once to
`xw` the way market/owner can — it would have to be passed per call
regardless of how `Crosswalk` is restructured, because it genuinely does
not exist until that specific row is reached in the replay loop. This is
the honest survivor of "needs decision-time context" — not "it currently
isn't passed in" but "the value being asked for (identity at a specific
historical moment) is defined per-row and has no run-level fixed answer."
Correctly already excluded from items 1-9 in the original inventory
above; restated here so it isn't mistaken for a second instance of the
same excuse this revision just dismantled elsewhere.

### Revised proposed approach

1. Fix item 2 (`Scorer.__init__`'s literal duplicate) immediately — still
   independent of everything else here.
2. Add `Crosswalk.attach_market(market, owner=None)`; call it once in
   `League.__init__` (after `self.owner` is final) and once at the top of
   `slate.py: slate_from_api`.
3. Merge `resolve_api` into `resolve` as shown above; delete
   `resolve_api`. Update the two call sites in `decide.py`.
4. Update call sites 1, 5, 6, 7 to call `resolve()` instead of `player()`
   (table above). Leave 3, 4 on `player()` — no name available, not a
   ladder. Update 10 (`_roster_key`) to drop its `market=` argument too,
   once `attach_market(market)` (no owner) is called at the top of
   `League.__init__`, before `replay()` — see the corrected #10 note
   above; this is not an exception after all.
5. Separately from this contract merge, still decide the upstream-timing
   mechanism (a) vs (b) from the prior revision, and move items 1, 5, 6,
   7 to resolve once at read time using whichever mechanism is chosen —
   that question is orthogonal to the contract merge above and stands as
   written before.
6. Verify every change against real data — frozen inputs, and where
   behavior is *expected* to change (the decide.py name-fallback fix,
   the cross-cutting price sanity check), verify the new output is
   correct by inspection, not that it matches the old output.

### Success test

For every call site: is there exactly one call, with no market/owner/
index argument that isn't either a per-row field (name, price, club,
handle) or `xw` itself? Items 1, 5, 6, 7, 8, 9, **and 10** all reach this
bar — a second, later check (prompted by finding a false wall elsewhere
in this same document) found item 10's stated exception didn't survive
direct code reading either; see the corrected note above. `identify()`
(never counted among the ten call sites, and still correctly excluded) is
the one place this document has found where a per-row, non-attachable
value is real: a specific transaction's own historical date, not
"decision-time context" in general.

At least ten places independently walk raw rows and resolve each to a
canonical player key, each hand-writing the shape `for r in rows: key =
resolve(...); if not key: <skip or collect>`:

**Crosswalk-only (movable upstream):**
1. `ffcore/score.py: _per_jornada_current` — two separate walks (starters
   rows via `xw.player(ff_slug=..., name=...)`; perjornada rows via
   `pid if pid in xw.players else xw.player(name=...)`).
2. `ffcore/score.py: Scorer.__init__` — two MORE loops (building
   `self.second`, and `self.listed`/`self.start_pct`/`self.status`), each
   over `lineups.csv`/`second`-source rows, both containing the literal
   same 4-line snippet verbatim (`_by_ff_slug` lookup, else a
   unique-name fallback via `_name_keys`) — confirmed by reading the code
   directly today. This one is NOT entangled with the upstream-timing
   question below: it's the same resolution snippet duplicated twice in
   the same method, fixable immediately with one local helper.
3. `ffcore/score.py: _shots_by_jornada` — `xw.player(app_id=...)` per row
   over `load_api_stats()`.
4. `ffcore/score.py`'s understat row walk (`_forward_understat_rows`,
   already extracted this session as its own shared generator, but not
   previously counted in this family) — `xw.player(understat_id=uid)`
   per row.
5. `ffcore/lineupweight.py`'s fitting loop — `xw.player(ff_slug=slug,
   name=...)`.
6. `ffcore/second.py: resolve_second_source` — `xw.player(af_slug=...,
   name=...)`, falling back to `norm(name or slug)`.
7. `ffcore/startprob.py: observations()` — **misclassified in the first
   pass of this plan as a single-row resolver called from a caller's own
   loop; that was wrong.** Confirmed by reading it: `xw.player(ff_slug=
   slug, name=nm)` sits directly inside `observations()`'s own `for slug
   in sorted(...)` loop, over the same wide/narrow lineup data `second.py`
   and `Scorer.__init__` also resolve, two other ways.

**Needs decision-time context (can't move upstream as-is):**
8. `decide.py` (building `market_key` and the per-team loop) —
   `xw.resolve_api(name, manager, market, owner, index, market_value)`
   genuinely needs a built `League` (owner map, live value index) —
   confirmed unavailable at parse/tidy time.
9. `slate.py: slate_from_api` — only needs `xw` + a `Market` (no owner/
   roster), so it's arguably movable too; flagged provisional rather than
   settled either way pending the mechanism decision below.

Correctly ruled out (re-confirmed, not just re-asserted): `ffcore/
fixture.py`'s club resolution (different entity, resolved once per
distinct team post-aggregation over `grouped_sums()`'s output, not per
row) and `ffcore/league.py`'s `identify()`/`_roster_key()`/`txn_key()`
(genuinely single-row resolvers, called from `replay()`'s own loop).

**The upstream-timing question has a real ordering constraint.**
`run.py`'s `STAGES` runs `parse -> crosswalk -> ...` — the crosswalk
(players.csv/clubs.csv) is *built from* already-parsed tables
(`crosswalk.py: build_players()`), so it does not exist yet when those
tables are first written. "Resolve once, at read time" therefore cannot
mean baking a key into the raw CSVs at parse time — that table is written
before a crosswalk exists to resolve against. It has to be one of:
  (a) a new post-crosswalk stage that widens the tidy CSVs with a
      resolved column, using `tidy.py`'s existing `widen_csv()`; or
  (b) a lazily-cached transform in the tidy loader functions, invalidated
      on a compound (source-file, crosswalk-file) stamp — the same
      pattern `load_crosswalk()` itself already uses for its own caching.
Option (a) has a real staleness hazard: `run.py main()` supports running
a subset of stages, so re-running `crosswalk` alone (e.g. after fixing a
name clash) without also re-running the widening stage would leave a
stale baked-in key column with nothing to invalidate it. Option (b)
avoids this by construction, at the cost of being a cache rather than a
persisted column. **Decide (b) unless there's a concrete reason the
persisted column is needed, before writing any code.**

**Proposed approach**

1. Fix item 2 (`Scorer.__init__`'s literal duplicate) immediately —
   independent of every other decision here, lowest risk, highest
   confidence.
2. Decide the upstream mechanism (a) vs (b) above.
3. Move items 1, 3, 4, 5, 6, 7 upstream using that mechanism.
4. For items 8-9 (and 9 specifically, once its "provisional" status is
   resolved), extract a shared "walk rows, resolve, branch on hit/miss"
   generator so the loop skeleton stops being duplicated, WITHOUT
   claiming this is equivalent to items 1-7's fix — it isn't: a caller
   still has to supply the same bespoke hint context afterward.
5. Verify every change against real data — frozen inputs, byte-identical
   before/after, exactly the standard held all session.

**Success test, stated per-group so it can't be gamed by narrowing scope
after the fact**: for items 1-7, could a new consumer get the key by
reading a field, with zero resolve call of its own? That must be true.
For items 8-9, the honest bar is lower — a shared generator, not a
zero-resolve read — and the plan must not present the second kind of win
as if it were the first.

## Area 2 — six operations have two live entry points

**Status: done** (63e5cc9). Both modules deleted; the six operations
exist only as `Universe` members (`player_forecasts` is a cached
property); `candidates()` lost its fallback-only `expected` argument.

**Re-investigated in full this session (every line of `decide.py`,
`ffcore/candidates.py`, `ffcore/par.py`, `ffcore/schedule.py`,
`ffcore/pricing.py`, `ffcore/action.py` read end to end) — the dependency
picture the previous note implied (five ffcore modules all entangled
with `decide.py`) turned out to be wrong in a way that matters for the
fix. Recorded here rather than silently corrected.**

### What actually depends on what

Only **two** of the five ffcore modules are implicated. `ffcore/
schedule.py` (rounds_left, next_then_rest, first_jornada_per_player,
apply_fixtures, phantom_topup, phantom_fill), `ffcore/pricing.py`
(locked, burn, cash_price, respond) and `ffcore/action.py` (the `Action`
dataclass) have **zero** duplicated entry points and **zero** upward
imports of `decide.py` — `pricing.py`'s functions duck-type on `u`
(proven by its self-test using a bare `_FakeUniverse`, not the real
`Universe`), and `schedule.py` operates on plain dicts (`base`, `club`,
`pos`, squads) with no `Universe` in sight. These three are a genuine,
correctly-drawn boundary: real reuse (`schedule.py`/`pricing.py` are
each called from exactly the places that need that specific plain-data
transform), not an accident. **Leave them exactly where they are.**

The cycle is narrower and uglier than "five modules import decide.py
back": only `ffcore/candidates.py` does. Its `candidates()` needs
`current_xi`, `xi_bar`, `route_kind` (lazy-imported from `decide`);
`fieldable_spares()` and `overdraft_fix()` need `_fieldable` (same, lazy
import). `ffcore/par.py`'s `player_forecasts()` does NOT import
`decide.py` at all — it duck-types on `u.state`/`u.forecaster`/
`u.view`/`u.players` exactly like `pricing.py` does, and only imports
`decide.Universe` in its own self-test. So `par.py` has no real cycle;
it has the *same* duplication symptom (a free function shadowed by a
cached `Universe` method) for a different reason — pure mechanical
copy-paste when `decide.py` was split, not an import-direction problem.

And a third fact the old note didn't distinguish: three of the six
duplicated names (`xi_bar`, `route_kind`, `rank`) are duplicated **inside
`decide.py` itself** — the free function and the delegating method are
defined a few lines apart in the same file (e.g. `Universe.xi_bar` is
literally `return xi_bar(*self.current_xi)`, both in `decide.py`). There
is no cross-file cycle to resolve for these three at all; it's a
same-file wrapper that serves no purpose.

Call-site check (the old note flagged this as "reportedly" true —
confirmed directly): `sim.py`, `slate.py`, `backtest.py` all call through
`Universe` methods (`u.xi_bar`, `u.dead_weight()`, `u.rank(...)`,
`u.player_forecasts()`), but `sim.py` ALSO imports and calls the free
functions directly in the same file — `from decide import dead_weight,
overdraft_fix, route_kind, value_rate` at its top, then both
`u.dead_weight()` (line 130) and bare `dead_weight(u)` (lines 898, 1092,
1200, 1205, 1208), both `u.xi_bar` and bare `route_kind(u, k)` (lines
228, 241, 612, 664) in the same file. This is not a hypothetical harm —
the duplication has already leaked into a real call site choosing
whichever spelling was convenient at each call, which is exactly what
"two entry points" predicts will happen over time. `report.py` and
`flip.py`/`scout.py`/`methodology.py` only import `decide`/`Universe`
generically (no free-function imports), so they're unaffected either
way.

### The six contracts, one sentence each

- **`xi_bar`** — the lowest expected points among the players in a
  manager's current best starting XI: the floor a signing must clear to
  be worth displacing someone.
- **`route_kind`** — classifies a player key by acquisition path
  relative to `me`: already mine, free, raidable-by-clause from a rival,
  or market-listed (and therefore off-limits to `candidates`).
- **`dead_weight`** — ranks my own non-starting squad players by resale
  proceeds: who's safe to sell because they never make my best XI.
- **`candidates`** — enumerates every affordable buy/clause/swap action
  given cash, the `xi_bar` floor, and `route_kind`, pairing each target
  with the cheapest spare that can fund it.
- **`player_forecasts`** — every player's season and next-jornada point
  forecast plus points-above-replacement (PAR), cached because the
  report layer asks twice per run.
- **`rank`** — scores a list of candidate actions by simulation (cheap
  screen → price the going cash rate → two reliability/value top-ups →
  expensive re-simulation of survivors), producing the ranked
  recommendation rows the CLI and reports print.

All six read multiple `Universe` fields at once (`state.squads`,
`state.jornadas`, `forecaster`, `view(...)`, `players`, `cash`,
`rival_cash`, `me` — in different combinations per operation) and two of
them (`current_xi`, `player_forecasts`) are load-bearing caches keyed off
`self.__dict__`. None of them has a natural 2-or-3-argument slice that
would make option (c) (narrow the signature to specific fields) a real
simplification — it would just make every call site rebuild a partial
`Universe` from a full one it already holds, for no benefit, and would
break the caching. Ruled out.

### Recommendation: (b), but precisely — merge the two contentious
### modules into `decide.py`; leave `schedule.py`/`pricing.py`/`action.py`
### alone; keep the `Universe`-method spelling, delete the free functions

The split of `candidates.py` and `par.py` out of `decide.py` was
line-count-motivated, not a real seam: `candidates.py` cannot even be
imported and used without reaching back into `decide.py` for four
names, hidden inside function bodies specifically so the module-load-time
import wouldn't cycle. That is the signature of a module that isn't a
module — it's a `decide.py` implementation detail wearing a file-system
boundary. `par.py` has no cycle, but it exists for the same "shrink
decide.py" reason and duplicates a `Universe` method for no reason at
all (unlike `pricing.py`, which never grew a duplicate). `schedule.py`
and `pricing.py` earn their separateness by taking plain data and never
touching `Universe` or `decide.py`; `candidates.py` and `par.py` do not.

Concretely:

1. **Fold `ffcore/candidates.py` and `ffcore/par.py` into `decide.py`.**
   Their contents (`candidates`, `dead_weight`, `overdraft_fix`, `apply`,
   `offer_combos`, `fieldable_spares`, `max_spare_proceeds`,
   `player_forecasts`, `value_rate`) become ordinary top-level names in
   `decide.py`, next to `Universe`. Delete both files.
2. **The four lazy, function-body imports in `candidates.py`
   (`from decide import _fieldable` / `current_xi, xi_bar, route_kind`)
   become nothing** — they're calls to sibling names in the same file
   now. This is the actual fix to "hidden dependency you can't see from
   a file header": there is no longer a header that could hide it.
3. **For the six duplicated operations, keep the `Universe`-method
   spelling and delete the free function.** Method form is the more
   ergonomic one at almost every real call site today (`u.xi_bar` and
   `u.player_forecasts()` are cached properties/memoized methods —
   deleting the method and keeping only the free function would either
   lose that caching or duplicate the cache-check at every call site),
   and it's already what `backtest.py`, `slate.py`, and most of `sim.py`
   write. So: `Universe.xi_bar`, `Universe.route_kind`, `Universe.
   dead_weight`, `Universe.candidates`, `Universe.player_forecasts`,
   `Universe.rank` become the ONLY definitions — their current bodies
   (today's free functions) move directly into the method, and the
   free-standing `xi_bar()`/`route_kind()`/`dead_weight()`/
   `candidates()`/`player_forecasts()`/`rank()` module-level functions
   are deleted outright, not kept as forwarders.
4. **Non-duplicated helpers stay as plain functions**, just physically
   relocated into `decide.py`: `apply(u, a)`, `overdraft_fix(u)`,
   `offer_combos(u)`, `fieldable_spares(u)`, `max_spare_proceeds(u)`,
   `value_rate(pts, cost)`. These never had a `Universe`-method twin, so
   there's nothing to collapse — moving the file is enough.
5. **`ffcore/schedule.py`, `ffcore/pricing.py`, `ffcore/action.py`:
   untouched.** They already have exactly one entry point each and no
   upward import; re-verified this session, not re-asserted.

### Call-site fallout (verified, not assumed)

- `backtest.py` (`u.xi_bar`, `u.route_kind(c)`, `u.rank(acts)`) and
  `slate.py` (`u.player_forecasts()`) already use only the method form —
  **no changes needed.**
- `sim.py` needs real edits: drop `from decide import dead_weight,
  overdraft_fix, route_kind, value_rate` (keep `overdraft_fix` and
  `value_rate` importable — they're not part of the six, only
  relocated — drop `dead_weight` and `route_kind` from that import since
  they're being deleted as free functions) and change every bare
  `dead_weight(u)` → `u.dead_weight()` (lines 898, 1092, 1200, 1205,
  1208) and every bare `route_kind(u, k)` → `u.route_kind(k)` (lines
  228, 241, 612, 664); the local `from ffcore.candidates import
  max_spare_proceeds` (line 134) becomes `from decide import
  max_spare_proceeds`; the local `from decide import Action, Universe,
  dead_weight` (line 1004) drops `dead_weight`.
- `ffcore/fixtures.py`'s lazy `from decide import Universe` / `from
  decide import _fieldable` (test-fixture builders constructing
  `Universe` instances) are unaffected — they were never part of the
  cycle, just ordinary late imports in fixture code that needs the real
  class.
- `report.py`, `flip.py`, `scout.py`, `methodology.py` import `decide`/
  `Universe` generically and are unaffected.

### Why not (a) or a from-scratch (d)

(a), moving `Universe` down into `ffcore/`, was considered and rejected:
`Universe` is decide.py's central object and `decide.py`'s own
non-duplicated logic (`load()`, `_current_xi`, `_score_many`, `_top_up`,
`band`, `paired`, `_selftest`, the `__main__` CLI) would have to move
with it or split awkwardly across a boundary — it recreates exactly the
"mechanical split" problem this area exists to undo, just in the other
direction. No fourth option beats (b): the dependency graph, once
correctly mapped, shows only two modules ever needed to reach back into
`decide.py`, and both do so only because their content IS decide.py's
content.

## Areas checked this session and correctly left alone

- **Store layer** (`load`/`load_api`, `write_csv`/`append_csv`/
  `csv_string`) — already unified, two documented exceptions where a
  caller must write even on empty rows.
- **Parsing layer** — JSON-list and single-selector-dedup HTML sources
  are unified behind shared engines. The multi-phase/stateful HTML
  parsers (`parse_team`, `parse_af_team`, `parse_starters`, `parse_market`,
  `parse_fitness`, `parse_elo`) were originally spot-checked and called
  "genuinely distinct, not missed mergers" at a weaker standard than the
  rest of this document; **re-audited at the full standard in Area 5**,
  which mostly confirms that verdict but found one real, small,
  previously-undetected duplication (an identical 9-key lineup-row
  envelope built three times across `parse_team`/`parse_af_team`) plus a
  smaller secondary candidate in `parse_fitness`. See Area 5, not this
  line, for the actual evidence.
- **Test fixture builders** (`tiny_state`/`tiny_bootstrap` share
  `_with_overrides`; `tiny_profile` genuinely differs, routes into two
  destination dicts).
- **`rank()`'s complexity** — see Area 4, which re-checked this verdict
  after Area 2's consolidation rather than leaving the pre-consolidation
  note standing unexamined.

## Area 3 — the fit_* family and the report-section builders, re-examined against the contract standard

**The prior note on both of these ("Fit-from-real-data family" / "Report-
section builders", quoted below before being struck) was reached by
asking "does the code look the same" rather than "should the
architecture make these the same kind of object" — the standard this
session was told twice to apply instead. Re-read end to end (all five
fit_* bodies, `_fit_lines`, all six section builders, and `main()`'s
assembly) against that sharper standard. The two verdicts below are not
the same shape: the fit family really does resist a Strategy-pattern
merge, for a structural reason, not a cosmetic one — but it hides one
real, small, currently-undetected duplication. The section builders
resist a generic renderer for four of six, but the doc's blanket "no
shared logic" claim was simply wrong about the other two.

### The fit_* family: what the contract actually is, and why a Strategy shell doesn't survive contact with the bodies

**The one-sentence job, if designed fresh:** given a pool of real
observations and a stated default, decide whether there's enough signal
to override the default, and if so, by how much, via a pluggable
estimation strategy. That's a real, well-known shape (validate-then-
estimate). The question is whether these five bodies actually implement
that shape *as two separable steps* — because that separability is the
only thing that would make a Strategy protocol (`class Estimator:
def check(data) -> (bool, str); def estimate(data) -> (float, str)`)
a real win rather than ceremony wrapped around what's already there.

They don't separate. In every one of the four functions that do share
the (value, reason) return shape, the "not enough data" guard and the
"measure" step share live intermediate values, computed once, not
recomputable-for-free in a second protocol method:

- `fit_rate_rel_floor` (`methodology.py:164`): `mean = _stats.mean(real)`
  is computed to answer guard #2 ("pool mean measured as 0"), and is
  then the denominator of `cv`, which the *measurement* grid search
  needs. `rels` (the graded, pj-conditioned list) is built once and is
  simultaneously the guard #3 sample-size check (`len(rels) < min_pairs`)
  and the thing the grid search over `_var_at(floor)` closes over.
- `fit_promoted_discount` (`ffcore/score.py:84`): `graded()` is a single
  generator; `grouped_sums()` over it produces `num, den, n` which is
  simultaneously the guard (`den <= 0 or n < 1`) and the numerator/
  denominator the shrinkage formula (`(k*PROMOTED_DISCOUNT + n*measured)
  / (k+n)`) measures from.
- `_fit_decay` (`ffcore/score.py:456`): `walk_error(1.0)` (the no-decay
  baseline) IS the guard (`base_n == 0`) and also the number the grid
  search's candidates are compared against to decide whether any decay
  "beat" it — guard and measurement are the same walk-forward validation
  loop run at different grid points, not two different kinds of work.
- `drift_frac_from_history` / `fit_drift_frac` (`methodology.py:209`,
  `ffcore/forecast.py:25`): `z1`, `z3` (the log-ratio z-scores) are built
  once; the length check IS the guard, and `statistics.pvariance(z1)`/
  `pvariance(z3)` — the measurement — are computed on those same lists.

Splitting any of these into a `check()` that returns a bool plus a
separate `estimate()` that redoes (or is handed) the same intermediate
arrays is not a simplification — it either recomputes the expensive part
twice, or turns the "shell" into a pass-through that hands the strategy
object its own precomputed state back, at which point the shell owns
nothing and the two methods could always have been one function, which
they already are. **This is the honest version of "genuinely different
statistical methods underneath" — not asserted, demonstrated per
function above.** Verdict: no Strategy-pattern merge. Five (four, see
below) separate functions is the correct shape here, and forcing a
protocol onto them would be exactly the "structure onto genuinely
different data" the user warned against.

**One correction to the prior note's premise, not just its conclusion:**
`lineupweight.fit_status_factors` (`ffcore/lineupweight.py:110`) does
**not** share "the" contract at all — there was never a fifth
implementation of the same shape to fold in. It returns
`{status: (ratio, n)}`, a dict with entries *silently omitted* below
`MIN_STATUS_ROWS`, not a `(value, reason)` tuple; it has no shipped
default to fall back to and no explanation string. Compare
`fit_lineup_weight` in the same file (`lineupweight.py:96`), which *does*
faithfully implement the shared shape (guard on `len(p) < MIN_ROWS` ->
`(None, reason)`, else grid-search walk-forward validation -> `(best,
reason)`) and wasn't even named in the prior note. The prior note's
inventory was assembled by name-matching ("things called `fit_*`"), which
caught the wrong member of the pair and missed the right one. Restating
the family precisely: `fit_rate_rel_floor`, `fit_drift_frac`/
`drift_frac_from_history`, `fit_promoted_discount`, `_fit_decay`,
`fit_lineup_weight` share the return contract; `fit_status_factors` is a
different job (a per-status lookup table with silent omission, not a
single override-the-default decision) wearing the family's name prefix,
and should stop being counted as a sixth (or fifth) instance of it in
any future note.

**The one real, small duplication this pass found, that the "five
different methods" framing was correctly not distracted by, but also
never went looking for:** `fit_rate_rel_floor` and
`drift_frac_from_history` open with the identical three lines —

```python
locks = clock_history().round_locks
actuals, _label = load_actuals()
preds = load_predictions()
```

— before diverging into different `lagged_pair(...)` calls and different
measurement. And they are called back to back, on the same data, in the
same run: `decide.py:434-436` calls
`_methodology.drift_frac_from_history()` immediately followed by
`_methodology.fit_rate_rel_floor(pool)`. Today that means
`load_actuals()` and `load_predictions()` — both real CSV reads — run
**twice** per report/decide run, for the identical underlying tables,
because the shared preamble was never factored out. This is the genuine
"same job wearing different clothes" case in this family: not the
estimation methods (those really do differ), but the history-loading
step that feeds them. Fix:

```python
def _graded_history() -> tuple[dict, list[dict], dict]:
    """(round_locks, actuals, predictions) -- the common real-data load
    fit_rate_rel_floor() and drift_frac_from_history() each open with,
    before diverging into different lagged_pair() calls. Shared because
    it is the same three loads, not because the fits are the same job."""
    locks = clock_history().round_locks
    actuals, _label = load_actuals()
    preds = load_predictions()
    return locks, actuals, preds
```

`fit_rate_rel_floor` and `drift_frac_from_history` both start with
`locks, actuals, preds = _graded_history()` in place of their three
duplicate lines. Better still, since `decide.py` calls both in immediate
succession on the same underlying data: have `decide.py` call
`_graded_history()` once and pass `locks, actuals, preds` into both
fitters (both functions gain optional `history=None` parameters,
defaulting to calling `_graded_history()` themselves so every other
existing caller — including `_selftest`, which calls `drift_frac_from_
history()` with no arguments — keeps working unchanged). This is a real
behavior change worth naming: today's run does two redundant passes over
`squad_log.csv`/`perjornada_*.csv`; after this, one. No output changes,
only a wasted read removed — a pure efficiency fix riding along with the
duplication fix, not a new number anywhere.

### Report-section builders: four are genuinely distinct, two were never a second and third thing

The prior note asserted "no shared logic between them" across all six.
Read against `_fit_lines`, that's not true for `drift_lines` and
`rate_rel_floor_lines` — it was never true, and `_fit_lines` is the
proof sitting right next to them:

```python
def drift_lines() -> list[str]:
    from ffcore.forecast import DRIFT_FRAC as _DEFAULT
    fitted, why = drift_frac_from_history()
    return _fit_lines("Season-long drift", fitted, _DEFAULT,
                      ("not enough",), why)

def rate_rel_floor_lines(pool) -> list[str]:
    from ffcore.forecast import RATE_REL_FLOOR as _DEFAULT
    fitted, why = fit_rate_rel_floor(pool)
    return _fit_lines("Rate uncertainty floor", fitted, _DEFAULT,
                      ("too few", "0 "), why)
```

Both bodies are, in full: call one fitter, call `_fit_lines` with a
heading, a default, and a tuple of "still unfitted" marker strings. There
is no second line of original logic in either function — the entire
"section" is a config record (heading, default, markers) plus which
fitter to call. That is not "a real, distinct section... confirmed"; it
is the *same* section (render one fit result as a markdown block)
instantiated twice, and giving each instantiation its own top-level name
is the identical mistake Area 1 found in identity resolution: a shared
ladder wearing two different call shapes because each grew from its own
caller rather than from what the job needed. Fix: delete both functions;
inline their two-line bodies as two `_fit_lines(...)` calls directly in
`main()`, next to each other, where the near-identical shape is visible
instead of hidden behind two names:

```python
def main() -> None:
    out = ["# How the forecast works — and how it's doing", ""]
    out += feed_lines()
    out += formula_lines()
    out += column_guide_lines()
    out += comparison_lines()
    fitted, why = drift_frac_from_history()
    out += _fit_lines("Season-long drift", fitted, _forecast.DRIFT_FRAC,
                      ("not enough",), why)
    fc = _fc()
    if fc is not None:
        fitted, why = fit_rate_rel_floor(fc.pool)
        out += _fit_lines("Rate uncertainty floor", fitted,
                          _forecast.RATE_REL_FLOOR, ("too few", "0 "), why)
    out += source_lines(load_actuals()[0])
    ...
```

(`_forecast` here is whatever import alias `methodology.py` already uses
for `ffcore.forecast` at call time — `drift_lines`/`rate_rel_floor_lines`
already did their own local `from ffcore.forecast import ... as _DEFAULT`
imports for exactly this constant, so this is not a new import, just
moved up two stack frames.) Behavior is bit-for-bit identical — this is
pure deletion of two pass-through names, not a contract change, which is
the correct outcome when the honest job of both functions is "we already
built the shared helper, stop wrapping it twice."

The other four — `feed_lines`, `formula_lines`, `comparison_lines`,
`source_lines` (which itself composes `start_lines`, `_instance_briers`,
`baseline_check`, `golden_rows`) — were checked against the specific
alternative the task asked about: could one generic renderer plus a
declarative per-section spec (title, columns, row source) replace them?
No, and the reason is concrete, not aesthetic:

- `feed_lines` iterates one row source (`sorted(FILLS)`) into one table —
  the closest of the four to "declarative table" shape — but every
  column value comes from a different stateful lookup
  (`_hosts()`/`_feed_state()`/`_fetched()`/`_age()`/`_light()`, several
  of which read live filesystem/state, not the row itself) and the
  freshness "light" is a five-way branch over cadence, staleness bounds,
  and API-page fan-out, not a formatting function of one field. A
  "column spec" here would just be a dict wrapping this same branch —
  no second caller exists to share it with.
- `formula_lines` isn't a table of rows over a collection at all: it's a
  fixed sequence of term/setting/fitted-status facts, several computed
  through entirely different subsystems (`fit_home_edge`, `elo_strength`,
  a memoized `_RATE_NOTE` global), assembled into one table by hand
  because there is no repeating row shape to declare a spec over.
- `comparison_lines` mixes prose (verdict sentences built from
  `stats.bootstrap_gap`), two *different* bucketed tables from the same
  `_bucket_means` helper (fixture-difficulty buckets and per-match
  buckets — already correctly sharing that helper, not duplicating it),
  and a top-5 "biggest miss" table sorted by a computed field. Three
  different tabular shapes plus narrative in one function, none of which
  match `feed_lines`' or each other's row/column shape.
- `source_lines`/`start_lines` compose several already-distinct
  statistical comparisons (start-rate Brier scores by source, a "fair
  comparison" restricted to shared instances, a golden-row baseline
  check) with early-return branches specific to what data exists this
  run. This is the report layer's most complex section and the furthest
  from "list of rows with a column spec."

Forcing a generic renderer over these four would require the "row
source" to already be a list of uniform records, which none of them are
until well after their own bespoke statistics have run — the spec
wouldn't eliminate logic, it would just rename the same branches as
"column functions" passed into a dispatcher with one caller each.
**Confirmed distinct, this time with the actual reason (four different,
non-uniform data shapes with a single caller each) rather than an
assertion that they are.**

## Area 4 — checked whether Area 2's consolidation reveals new duplication between `candidates()`/`rank()` and `sim.py`'s second screen; mostly it doesn't, but it found one real small one

**Status: done** (63e5cc9), minus two items dropped: `_could_spare()`
(overdraft_fix needs the trial dict itself, not a bool) and the `_top_up`
comment (comments stripped in c53fb77). `_best` is now `best_move`.

**This area exists because Area 1 and the old Area 2 note were each once wrong
in the same specific way — cross-file distance hiding (Area 1) or inventing
(old Area 2) contract overlap — so once `candidates()`/`dead_weight()`/
`rank()`/`route_kind()`/`xi_bar` become same-file siblings, the same question
had to be asked of them: does being siblings now expose something living in
different files hid? Read `candidates()`, `dead_weight()`, `overdraft_fix()`,
`fieldable_spares()`, `rank()`, `_top_up()`, `route_kind()` end to end again,
plus `sim.py`'s `worth_doing()`, `_clears_par_floor()`, `raid_shortlist()`,
`_gains()`, `_best()`, `ladder_rows()`. Four questions asked, four answers:**

### 1. `candidates()`'s route_kind filter vs `rank()`'s `_top_up` "listed" check — NOT a duplicate, confirmed by tracing what `.get(..., default)` actually falls back to

`candidates()` calls `route_kind(u, c)` and skips anything it classifies
`"listed"` — so by the time `rank()` receives `acts` from the real pipeline
(`u.candidates(...)` feeding `u.rank(acts, ...)`, `sim.py:1639-1651`), no
action's `buy` can have `route_kind == "listed"`. That looked, on its face,
like `_top_up`'s `ok=lambda d, a: u.view("route").get(a.buy, "free") !=
"listed"` must be dead code re-deriving a filter that already ran. It isn't,
for a reason only visible by reading both functions' exact field access
side by side:

- `route_kind(u, k)` reads **ownership** (`u.view("owner")`, from the
  ledger's tracked-owner map) first; only for players it decides are
  rival-owned does it consult the raw `route` field (default `"market"`) to
  ask "is this specifically raidable by clause." A player with NO tracked
  owner is `"free"` regardless of what the raw `route` field says.
- `_top_up`'s check reads the raw `route` field directly (default `"free"`)
  with no ownership lookup at all. It is asking a narrower, different
  question: **is this specific candidate an active market listing right
  now** (`route == "listed"`, set by `market_routes()` in `tidy.py` when
  `seller == LISTED_SELLER`) **— independent of whether anyone in the
  tracked owner map claims him.**

Those disagree on exactly the case that matters: a player actively listed
for sale by a manager the ledger doesn't track as an owner (an unjoined
squad, an API-only manager) is `"free"` under `route_kind` — so `candidates()`
correctly offers him as a buy — but his raw `route` is still `"listed"`, so
`_top_up` correctly treats him as less reliable than a true free agent:
he's a live auction someone else could win or reprice before the bid lands,
which `route_kind`'s ownership-first classification has no way to see.
**This is two different questions wearing the same word ("listed") and the
same field name ("route"), not one job split in two** — route_kind asks
"who can I acquire this from," `_top_up`'s check asks "how contestable is
this specific market row." Confirmed reachable in production, not just in
the self-test's hand-built `acts5`: any market-listed row from an
untracked seller flows through the real `u.candidates()` → `u.rank()` path
unfiltered by `route_kind` and only gets the reliability penalty from
`_top_up`. **No change recommended** — but the two different `.get(...,
default)` fallback values (`"market"` vs `"free"`) doing different jobs on
the same-named field is exactly the kind of thing a future reader will
mistake for the duplication it isn't, so a one-line comment at `_top_up`'s
`ok=` lambda (pointing at this paragraph's distinction) is worth adding
when the consolidation patch lands — cheap, and it directly forestalls the
misreading this investigation itself started with.

### 2. "Who's safe to sell to fund this" is three different questions, not one computed three times — except one small piece really is copy-pasted

Three functions look like they overlap and don't, plus one line that
genuinely does:

- **`dead_weight()`**: ranks squad members who never start (across every
  remaining jornada) by resale proceeds, descending. Answers "who can I
  sell for the most immediate cash." Used for the ladder's SELL group and
  as `overdraft_fix()`'s candidate order.
- **`candidates()`'s internal `spare` list** (`fieldable_spares()` filtered
  through `_spare_rank`, ranked by `value_rate(par, proceeds)` ascending):
  answers "if I fund THIS specific buy with one of my own players, which
  one costs me the least points-per-euro to give up" — an opportunity-cost
  ranking, not a cash-magnitude one, and eligibility is "still fieldable
  without him" (a structural, single-player trial), not "never starts" (a
  performance criterion). A player can be structurally sparable but very
  much a starter (fails `dead_weight`'s test) or vice versa.
- **`overdraft_fix()`**: walks `dead_weight()`'s cash-ranked order,
  sequentially removing players and re-checking `_fieldable` after each
  removal, until the shortfall clears. This is inherently sequential and
  can't reuse `fieldable_spares()`'s output directly — `fieldable_spares()`
  only tests single removals against the untouched squad, and a second
  sale can turn a previously-legal single removal into an illegal one.

These are genuinely three different jobs (performance vs. efficiency vs.
sequential-clearance) and should stay separate — this is not "identity
resolution wearing four call shapes," it's three real questions.

**The one real duplicate**: `fieldable_spares()`'s body —
`{p: s for p, s in mine_squad.items() if p != k}` then `_fieldable(trial)`
— and `overdraft_fix()`'s loop body do the exact same "remove this one
player, is what's left still fieldable" check, written twice
(`ffcore/candidates.py:13-15` and `:89-91`, moving to sibling positions in
`decide.py` under Area 2). Worth a one-line shared helper once they're
siblings — e.g. `_could_spare(squad, k) -> bool` — called from both
`fieldable_spares()`'s comprehension and `overdraft_fix()`'s loop. Small,
genuinely mechanical, in scope for the Area 2 patch (not a separate
follow-up) since it only becomes a same-file, three-line fix once both
functions land in `decide.py` together.

### 3. `rank()` → `worth_doing()` — the stated reason for keeping these apart was checked directly and is FALSE; the real question is still open

The prior pass in this section (and the agent that wrote it) gave one
concrete reason not to fold `worth_doing()`'s policy rules into `rank()`/
`Universe`: the PAR floor needs `methodology.current_mae()`, "a module
`decide.py` doesn't and shouldn't import." **Checked directly against the
real code, this is false, not an approximation.** `decide.py` already
imports `methodology` at the top of the file
(`import methodology as _methodology`, `decide.py:14`) and already calls
it in real production code, not a test: `decide.py:434-436`, inside the
real `load()` function, calls `_methodology.drift_frac_from_history()`
and `_methodology.fit_rate_rel_floor(pool)` directly. There already IS a
live cycle (`decide` imports `methodology` at module level;
`methodology`'s `_fc()` imports `decide` lazily, inside a function, to
get `decide.load().forecaster`) — it already works today, resolved the
same way Area 2 resolved `decide`↔`candidates.py`: the side that would
create a load-time cycle stays lazy. So `worth_doing()` calling
`_methodology.current_mae()` from inside `decide.py` would use an import
that already exists, not create a new one. **The specific technical wall
this section rested on does not exist.**

What's still true and still real, separate from that false premise:
`rank()`'s `_top_up`/`KEEP` machinery answers "which candidates are worth
a second, expensive simulation pass" (computational-budget triage), and
`worth_doing()`'s three rules (PAR floor, one-raid-per-victim, sign check)
answer "is this candidate worth recommending" — genuinely different
questions, and the sign check specifically needs `rank()`'s own
FINAL-trial output to exist first, so it cannot run *during* `rank()`'s
screen pass regardless of import structure. **This is a real sequencing
constraint** (worth_doing must run after rank returns), but sequencing is
not the same claim as "different modules, can't be siblings" — `apply()`,
`overdraft_fix()` etc. all run in sequence within `decide.py` already.

**Checked directly, not left open**: does anything call `worth_doing()`
on data that didn't come from `Universe.rank()`? No. `worth_doing()` has
exactly two real (non-selftest) call sites in the whole codebase
(`sim.py:222`, inside `ladder_rows()`; `sim.py:850`, the headline path),
and both receive rows that trace directly back to `u.rank(...)`'s return
value (`sim.py:1650`: `rows, base, measured, bands = u.rank(acts, ...)`,
then `ladder_rows(u, rows, bands, ...)`, which is where both call sites
get their input). There is no independent product use of `worth_doing()`
— the "keep it separate in case it's needed standalone" case does not
exist in this codebase today.

**Recommendation, given both the false import-wall and the absent
product need are now cleared**: fold `worth_doing()`, `raid_shortlist()`,
`_gains()`, `_clears_par_floor()`, `_best()` into `decide.py` as plain
functions (or `Universe` methods, matching whichever the Area 2 patch
settles on for similarly-shaped helpers) taking `rank()`'s output as their
argument, called immediately after — the one hard constraint that
survives (the sign check needs FINAL-trial `d_pts`, so this must run
after `rank()` returns, never during) is a sequencing fact, not a
module-boundary fact, and `decide.py` already sequences `apply()` after
`candidates()` this way. This is a genuine, evidence-backed simplification
now, not an assumption — reverses the prior "leave the split exactly as
it is" verdict, which was reached on the false premise this section
corrected above.

### 4. Pipeline-object question — resolved by #3's finding, not a separate one

Given #3's answer (fold `worth_doing()` and its siblings into `decide.py`,
called right after `rank()`), there's no remaining case for a wrapper
*object* that welds them into one call — that would just be reintroducing
ceremony around a plain two-line sequence (`rows = self.rank(acts); rows =
_screen(self, rows)` or equivalent) once both live in the same file with
no boundary between them to paper over. No pipeline object; the
consolidation from #3 already gets the simplification a wrapper object
would have been trying to fake.

### rank()'s complexity verdict (re-confirmed under the sharper standard)

The existing "checked and correctly left alone" verdict on `rank()`'s
two-pass screen/re-simulate pipeline was written before Area 2's
consolidation was decided; re-reading it now, side by side with
`candidates()`, `dead_weight()`, and `sim.py`'s second screen, changes
nothing about that verdict — if anything it's better evidenced now: none
of `rank()`'s internal complexity (the cash-price fit, the two `_top_up`
passes, the pick-best-per-key dedup) duplicates anything in `candidates()`
or `worth_doing()`, each of which was checked line-by-line against it above
for exactly that. Real, appropriately-scoped complexity — not hidden
duplication, and not a decision that needed revisiting once these
functions become siblings.


## Area 5 — `src/sources.py`'s parser/sign layer, re-audited against the sharper standard (earlier this session's pass on this file was weaker: it spot-checked and stopped at "needs different inputs")

**Status: done** (fb1a912, 4ac3d81 -- `parse_fitness` became one
(element, status) table and one loop, no `_scan()` helper).

**Read every parser and sign function in the file end to end (~2,700 lines,
29 parser/sign functions), plus the shared engines already in place
(`_parse_json_list`, `_extract_rows`, `_sign_elements`, `_sign_links`,
`_sign_rows`, `_once`, `_player_identity`), specifically to re-check the
prior verdict recorded under "Areas checked this session and correctly
left alone": that `parse_team`, `parse_af_team`, `parse_starters`,
`parse_market`, `parse_fitness`, `parse_elo` are "genuinely distinct, not
missed mergers." That verdict was reached by spot-checking a few pairs
and accepting "different markup, different fields" as a stopping point —
exactly the standard Areas 1 and 4 later learned to distrust. Re-derived
here by reading every body in full and diffing them field-by-field, not
by re-citing the earlier note.**

### The six parsers really are mechanically distinct — but the verdict was checked, not just repeated

Each of the six operates on a genuinely different input shape and
produces a genuinely different row schema or merge semantics:

- `parse_market` never builds an lxml tree at all — it splits the raw
  HTML string on a literal marker (`'class="elemento_jugador'`) and pulls
  fields via `data-*` regex attributes (`_attr`). No dedup, no `_once`,
  no role concept. Mechanically unrelated to the other five.
- `parse_fitness` takes an **already-parsed** `doc`, not raw HTML (its
  caller, `parse_team`, builds the tree once and passes it in) and
  returns a `dict` keyed by normalized name with severity-ranked
  overwrite semantics (`SEVERITY.index(prev) <= SEVERITY.index(new)`) —
  a genuinely different return contract (lookup table, not a row list)
  from every other parser in the file.
- `parse_team` merges two things `parse_af_team` never touches: the
  `fitness` dict `parse_fitness` just built, and a post-loop injection of
  synthetic "absent" rows for fitness entries no XI/subs selector ever
  produced a row for. `parse_af_team` has no fitness merge at all
  (`status` is always `""`) and instead has a two-source fallback
  (`AF_XI_SELECTOR`, then — only if that returned nothing — a
  unanimous/divided "consenso" block with its own nested-`ul` branching).
  These are two different real problems (FF publishes a fitness sidebar
  *and* an XI list that need reconciling; AF sometimes has no confirmed
  XI at all and falls back to a consensus poll), not the same problem
  solved twice.
- `parse_starters` produces an 8-key row (`match_id`, `minute`, no
  `status`/`note`/`start_pct`) built from a per-side XI-count validation
  (`sum(... role == "starter") != XI_SIZE`) that has no analogue in the
  other five at all.
- `parse_elo` extracts a `var vegaJson = {...}` blob embedded in a
  `<script>` tag via manual brace-matching JSON decode
  (`json.JSONDecoder().raw_decode`), filtered by country/level — no
  markup traversal whatsoever.

**Confirmed, not re-asserted: no Strategy-style merge survives contact
with these six bodies.** Forcing one would mean threading through a
name-extraction hook, a slug-extraction hook, a fitness-merge hook, a
same-schema-or-not question, and a dedup-key hook, each with exactly one
real caller — the same "protocol wrapping a single implementation" antipattern
Area 3 rejected for the fit_* family, for the identical reason: the steps
don't separate into interchangeable pieces, they're one function each.

### The one real duplication this pass found that the spot-check missed: an identical 9-key row envelope, written out three times

`parse_team` and `parse_af_team` do not just parse similarly — the
**output row schema is asserted identical by the file's own self-test**:

```python
assert list(af[0]) == list(rows[0])          # line 2070
...
assert list(con[0]) == list(rows[0])         # line 2089
```

Both functions build this exact 9-key dict — `observed_at`, `source`,
`team_slug`, `player_name`, `player_slug`, `role`, `start_pct`, `status`,
`note` — and they build it **three separate times**, not two:

1. `parse_team`'s `add(el, role)` closure (`sources.py:246-256`):
```python
rows.append({
    "observed_at": observed_at, "source": SOURCE, "team_slug": slug,
    "player_name": name,
    "player_slug": _slug('href="%s"' % href) if href else None,
    "role": role, "start_pct": pct,
    "status": fit["status"] if fit else "ok",
    "note": fit["note"] if fit else "",
})
```
2. `parse_team`'s post-loop "absent" injection (`sources.py:266-276`), a
   **second, separate literal of the same nine keys** inside the same
   function:
```python
rows.append({
    "observed_at": observed_at, "source": SOURCE, "team_slug": slug,
    "player_name": fit["name"], "player_slug": fit["slug"] or None,
    "role": "absent", "start_pct": None,
    "status": fit["status"], "note": fit["note"],
})
```
3. `parse_af_team`'s `add(name, img_src, role, start_pct, note)` closure
   (`sources.py:543-557`):
```python
rows.append({
    "observed_at": observed_at, "source": AF_SOURCE, "team_slug": slug,
    "player_name": name,
    "player_slug": m.group(1) if m else None,
    "role": role, "start_pct": start_pct,
    "status": "", "note": note,
})
```

This is the honest version of a real, small, currently undetected
duplication — the *envelope* is the same job (assemble a lineup row) done
three times, even though the *inputs feeding it* (name-extraction,
slug-extraction, fitness lookup) are correctly not shared. Fix: one
constructor, three call sites:

```python
def _lineup_row(observed_at, source, team_slug, name, slug, role, pct,
                status, note) -> dict:
    """The 9-key lineup-row envelope parse_team and parse_af_team both
    build -- proven identical by the self-test's own list(af[0]) ==
    list(rows[0]) assertion -- written out three times (parse_team's
    add(), its absent-row injection, and parse_af_team's add()) before
    this existed."""
    return {"observed_at": observed_at, "source": source,
            "team_slug": team_slug, "player_name": name,
            "player_slug": slug, "role": role, "start_pct": pct,
            "status": status, "note": note}
```

- `parse_team`'s `add()` becomes `rows.append(_lineup_row(observed_at,
  SOURCE, slug, name, _slug('href="%s"' % href) if href else None, role,
  pct, fit["status"] if fit else "ok", fit["note"] if fit else ""))`.
- `parse_team`'s absent-injection loop becomes `rows.append(_lineup_row(
  observed_at, SOURCE, slug, fit["name"], fit["slug"] or None, "absent",
  None, fit["status"], fit["note"]))`.
- `parse_af_team`'s `add()` becomes `rows.append(_lineup_row(observed_at,
  AF_SOURCE, slug, name, m.group(1) if m else None, role, start_pct, "",
  note))`.

**Behavior change: none.** Every field value fed into the constructor is
exactly the value the current literal computes; this is pure deletion of
a duplicated dict literal, verifiable byte-for-byte against the existing
self-test (`_selftest()` needs no changes — same keys, same values, same
order, and dict key order doesn't affect `==` or the `list(af[0]) ==
list(rows[0])` assertion, which is itself already the proof this envelope
is one contract, not two).

### A smaller, secondary candidate found in the same file: `parse_fitness`'s three status-scan loops

`parse_fitness` (`sources.py:190-222`) runs three loops over three
different selectors, each doing "extract name/slug, call `put(name, slug,
status, note)`" and differing in exactly one thing — how `status` for
that loop is derived:

```python
for el in _css(doc, ".lesionados_wrapper section.mod.lesionados > .elemento"):
    icon = _css(el, ".icono img")
    alt = (icon[0].get("alt") or "").strip().lower() if icon else ""
    status = FITNESS_ALT.get(alt)
    if not status:
        continue
    name, slug = _flagged_name(el)
    put(name, slug, status, _note(el))

for sec in _suspension_sections(doc):
    for el in _css(sec, ".elemento"):
        name, slug = _flagged_name(el)
        put(name, slug, "suspended", _note(el))

for el in _css(doc, "section.mod.nodisponibles .elemento"):
    name, slug = _flagged_name(el)
    put(name, slug, "unavailable", _note(el))
```

This is real, smaller-stakes duplication (three copies of `name, slug =
_flagged_name(el); put(name, slug, <status>, _note(el))`), collapsible
with one helper taking either a fixed status string or a per-element
callable:

```python
def _scan(elements, status_of) -> None:
    for el in elements:
        status = status_of(el) if callable(status_of) else status_of
        if not status:
            continue
        name, slug = _flagged_name(el)
        put(name, slug, status, _note(el))
```

Flagged as secondary (not folded into the primary recommendation above)
because the payoff is smaller — three one-line bodies, not a 9-key
literal repeated three times — and because the first loop's `status_of`
callable is non-trivial enough (`FITNESS_ALT.get(icon-alt-lookup)`) that
the win is closer to "same amount of code, differently arranged" than a
clear reduction. Worth doing in the same patch as the row-envelope fix
since it touches the same function, but not worth a special case if it
turns out awkward in practice — unlike the row envelope, which is
unambiguous.

### The rest of the file, re-verified rather than re-cited

- **`_parse_json_list`-based parsers** (`parse_api_leagues,
  parse_api_market, parse_api_activity, parse_api_teams,
  parse_api_players_all, parse_api_offer`) are correctly unified already —
  each supplies only a `row_fn`/`if_empty`, no duplicated skeleton.
  `parse_api_teams` is the one that looks like it might be hiding a
  second job (it emits three different `ROW_TABLE` values —
  `api_teams`/`api_standings`/`api_stats` — from one JSON tree) but this
  is one pass over one nested structure emitting a fixed fan-out per
  input row, not three parsers glued together; correctly left as one
  function.
- **`parse_api_lineup` and `parse_api_player`** correctly do NOT use
  `_parse_json_list` — both decode a JSON **object** via `_j_dict`, not a
  JSON **list** (`_parse_json_list` requires `isinstance(d, list)`).
  This is a real, checked reason (verified by reading both bodies against
  `_parse_json_list`'s guard clause), not an assumption: these are
  single-object detail endpoints, structurally incompatible with the
  list-of-items engine, not a missed application of it. Both already
  reuse `_player_identity` for their shared field set, which is the right
  amount of sharing.
- **The sign layer's split between `_sign_rows` (parse, then format rows)
  and `_sign_elements`/`_sign_links`/raw-regex digests
  (`sign_market`, `sign_team`, `sign_af_team`, `sign_starters`,
  `sign_calendar`, `sign_af_fixtures`)** is architecturally deliberate,
  confirmed by reading what each accepts: every parser wired to a
  surface-digest signer either can raise on malformed input it wasn't
  built to validate (`parse_market`'s `int(value)` has no guard) or
  discards rows a signer needs to still detect changes in (`parse_market`
  silently drops any chunk missing `name`/`value`, but a surface digest
  over `MARKET_SURFACE_RE` still changes if that chunk's other attributes
  change). A signer that called the full parser would inherit both
  problems — crash on bad data instead of just not-refreshing, and miss
  changes in rows the parser rejects. This is the same "genuinely
  different job wearing a similar name" shape Area 4 found for
  `route_kind` vs. `_top_up`'s route check: signing asks "did anything
  in the raw surface change," parsing asks "what does this data mean" —
  not one job split in two.
- **No identity-resolution touchpoint at all.** Confirmed by grep (`xw\.`,
  `Crosswalk`, `resolve(`, `.player(`, `resolve_api` — zero matches in
  `src/sources.py`). This file only ever produces raw, source-tagged rows
  keyed by whatever id that source natively uses (`ff_id`, `af_id`
  slug, `understat_id`, `player_id` from the LaLiga API); every one of
  Area 1's ten call sites lives downstream of it, in `ffcore/`. Correctly
  excluded from Area 1's inventory — there is nothing here that resolves
  a raw signal to a canonical player key at all.

### Success test

Every one of the six "multi-phase" parsers keeps its own body — no
generic renderer, no Strategy protocol, because none survives contact
with what the bodies actually do (demonstrated per-function above, not
asserted). The only change is `_lineup_row()`: does `parse_team` and
`parse_af_team`'s self-test (`list(af[0]) == list(rows[0])`,
`list(con[0]) == list(rows[0])`) still pass unmodified after the
constructor lands, with the three call sites producing byte-identical
dicts to what they build today? That must be true, and it's a strong
test precisely because the self-test already independently proved these
three literals were the same contract before this area ever looked at
the file.

### Estimated net change

One new 10-line helper (`_lineup_row`), three call sites collapsed from
~10 lines of dict literal each to 1 line each: roughly **-20 to -25 net
lines, +1 net function**. If the secondary `_scan()` candidate is taken
too: one more ~6-line helper, three loop bodies (3-5 lines each)
collapsed to 1 line each: another **-10 to -15 net lines, +1 net
function**. Combined, roughly **-30 to -40 lines, +2 functions** — a
small, real cleanup, not a rewrite; everything else in the file (23 of
the file's 29 parser/sign functions plus every shared engine) is
correctly left exactly as it is, verified this pass rather than carried
over from the earlier, weaker one.

## Area 6 — the report/decide-adjacent leaf modules (`squads.py`, `scout.py`, `gap_signal.py`, `report.py`, `flip.py`, `backtest.py`, `ffcore/score.py`'s non-fit_* content, `ffcore/season.py`, `ffcore/profile.py`, both `crosswalk.py` files, `xi.py`, `points.py`, `digest.py`) — one still-open item from Area 1, one real small duplicate found fresh, one cosmetic-looking duplicate correctly left alone, and everything else confirmed clean by reading, not by inheriting an earlier "reviewed" label

**Status: done** (3cb954e, b9796cf, 93a16b0; `app_fielded` takes the
crosswalk in 4ac3d81).

**Method used throughout this area, same as Areas 1-4: for each module,
what job is it doing (the contract), which functions implement it, and —
specifically — does any function here quietly re-implement a ladder or
helper this plan already named elsewhere (Area 1's identity resolution,
Area 2/4's `candidates()`/`rank()`/`route_kind` family), or hide a small
duplicate of its own that a "no shared logic" read would miss (Area 3's
class of finding). A prior pass in this session (`git show 9578de6`)
already inlined three genuinely single-caller helpers out of
`squads.py`/`scout.py`/`league.py` and explicitly left
`sec_premium`/`sec_drift`/`write_league`/`log_slate`
(`squads.py`) and `_jornada_dates`/`_status_history`/`_status_around`
(`gap_signal.py`) alone as "report/pipeline sections composed by one
caller each... no shared logic found." Re-read all of those bodies line
by line rather than trusting that verdict: it holds. The only shared
shape between `sec_premium` and `sec_drift` is `sorted(rows, key=lambda
r: r["date"], reverse=True)[:25]` (`squads.py:138`, `:166`) — a two-token
sort-and-slice idiom with no shared intermediate state, the same kind of
non-finding Area 3 was careful not to manufacture an abstraction around.
Correctly left alone.

### 1. Still open: Area 1's item 2 (`Scorer.__init__`'s literal duplicate) was never actually fixed

Area 1's plan (§"Proposed approach", step 1) said to fix this
"immediately... independent of everything else" the first time this
family was catalogued. Reading `ffcore/score.py` today, it is still there,
byte for byte:

```
738: k = self._by_ff_slug.get(norm(r.get("player_slug") or ""))
739: if not k:
740:     hits = self._name_keys.get(norm(r.get("player_name") or ""), [])
741:     k = hits[0] if len(hits) == 1 else None
```
— building `self.second` from `second` rows (`Scorer.__init__`,
`ffcore/score.py:737-743`) — and again, three lines later, verbatim
except for the variable name:
```
749: key = self._by_ff_slug.get(norm(r.get("player_slug") or ""))
750: if not key:
751:     hits = self._name_keys.get(norm(r.get("player_name") or ""), [])
752:     key = hits[0] if len(hits) == 1 else None
```
— building `self.listed`/`self.start_pct`/`self.status` from `xi` rows
(`:745-760`). Both walk a list of lineup-shaped rows and need "the
crosswalk key for this row, by ff_slug else by unique name" — a
single-row resolver, one rung short of `player()`'s full ladder because
neither row shape carries an app_id/af_slug/understat_id. This is not a
new finding; it's confirmation, by direct reading, that Area 1's lowest-
risk, already-agreed fix is still unimplemented. Fix (unchanged from
Area 1's original proposal, restated here because this is the module
where it actually lives):

```python
def _key_of(self, r: dict) -> str | None:
    """The crosswalk key for one lineup-shaped row: ff_slug if it names
    one uniquely, else the unique player this row's name matches --
    the same lookup self.second and self.listed/start_pct/status each
    built separately."""
    k = self._by_ff_slug.get(norm(r.get("player_slug") or ""))
    if k:
        return k
    hits = self._name_keys.get(norm(r.get("player_name") or ""), [])
    return hits[0] if len(hits) == 1 else None
```
Both loops call `self._key_of(r)` in place of their own three lines.
Behavior is unchanged — this is deletion of a repeated snippet, not a
contract change. **In scope for this area's patch, not a new decision**:
Area 1 already decided this; it just never landed.

### 2. A real, freshly-found duplicate: `backtest.py`'s `replay_ladder_percentile` re-derives `_replay()`'s own setup instead of sharing it

`_replay()` (`backtest.py:474-482`) is the shared entry point
`replay_recommendations()` and `replay_percentile_rank()` both call
(confirmed: `:485-490`) — it does exactly three things before delegating
to `_grade_episodes`: `commits_touching("reports/decisions.json")`,
bail if empty; `_actuals_index()`, bail if `now is None`; call
`_grade_episodes(_pick_episodes(commits, pick), points_between, now,
min_days)`. `replay_ladder_percentile` (`:493-505`) needs the *same*
`commits`/`points_between`/`now` triple, but for two different `pick`
functions graded against the same underlying data (`current` vs.
`percentile`) — so it can't just call `_replay()` twice without paying
for `commits_touching` (a `git log` subprocess call) and
`_actuals_index()` (a full walk of `load_actuals()`) a second time for no
reason. Instead of sharing `_replay()`'s setup, it re-writes the same
three lines inline:

```python
commits = commits_touching("reports/decisions.json")
points_between, now = _actuals_index()
if not commits or now is None:
    return {"current": None, "percentile": None}
```

This is the same shape Area 3 found in `fit_rate_rel_floor`/
`drift_frac_from_history` (a shared real-data preamble, written out
twice because nobody needed to call it twice from the same place until
now) — not a cosmetic echo, an actual redundant-load risk once a second
multi-pick caller exists (which `replay_ladder_percentile` already is).
Fix: extract the setup, keep `_replay()`'s single-pick shape as a thin
wrapper over it:

```python
def _replay_setup(min_days: float):
    """(commits, points_between, now), or None -- the real-data load
    every replay_*() function needs before it can grade any pick."""
    commits = commits_touching("reports/decisions.json")
    if not commits:
        return None
    points_between, now = _actuals_index()
    if now is None:
        return None
    return commits, points_between, now


def _replay(pick, min_days: float) -> dict | None:
    setup = _replay_setup(min_days)
    if setup is None:
        return None
    commits, points_between, now = setup
    return _grade_episodes(_pick_episodes(commits, pick), points_between,
                           now, min_days)


def replay_ladder_percentile(topn: int = 3, min_days: float = 3.0) -> dict:
    setup = _replay_setup(min_days)
    if setup is None:
        return {"current": None, "percentile": None}
    commits, points_between, now = setup

    def pick_current(moves):
        return [m for m in moves[:topn] if m.get("buy") and m.get("sell")]

    cur_eps = _pick_episodes(commits, pick_current)
    pct_eps = _pick_episodes(commits, lambda moves: _by_pts_lo(moves, topn))
    return {"current": _grade_episodes(cur_eps, points_between, now, min_days),
           "percentile": _grade_episodes(pct_eps, points_between, now, min_days)}
```
Behavior identical — `_replay_setup` inlines exactly what both call sites
already computed, just once each per call instead of duplicated as source
text. No output changes. This sits right next to the two merges
(`_checked_golden`/`_mae_result`, `_pick_episodes`/`_grade_episodes`)
already done in this file — those were done correctly and are not
re-litigated here; this is the one spot in `backtest.py` that pass
didn't reach because `replay_ladder_percentile` was added after, or was
not compared against `_replay()` at the time.

### 3. A small, low-value, genuine duplicate: the XI-search "shapes" list is built twice, once per file, because of import direction — not worth more than a one-line fix

`ffcore/score.py: pick_xi` (`:966-976`) recomputes, on every call:
```python
shapes = [{"POR": 1, "DEF": d, "MED": m, "DEL": f}
         for d, m, f in formations()]
```
`ffcore/season.py` (`:14-15`) has the **identical** list comprehension,
as a module-level constant `SHAPES`, built once. Both feed the same
`_xi_search()` (`score.py:917`) — the actual search engine is correctly
shared (this is not a repeat of Area 1's "same ladder, different
callers" pattern; the engine itself has exactly one implementation).
The list-of-shapes is the only thing duplicated, and it's duplicated
because `season.py` imports `MAX_SLOT, formations, _xi_search` **from**
`score.py` (`season.py:6`) — `score.py` cannot import `season.py`'s
`SHAPES` back without a cycle. This is a real, checked reason (not an
assumed one, per this plan's own standard), but the fix doesn't need to
break the import direction: move the constant to where `formations()`
already lives.

```python
# ffcore/score.py, right after formations():
SHAPES = [{"POR": 1, "DEF": d, "MED": m, "DEL": f}
         for d, m, f in formations()]
```
`season.py` then does `from ffcore.score import MAX_SLOT, formations,
SHAPES, _xi_search` and deletes its own `SHAPES = [...]`; `pick_xi` uses
the module-level `SHAPES` instead of rebuilding the list every call.
Small win (one list literal instead of two, a handful of allocations
saved per `pick_xi` call — `ffcore/bid.py` calls it up to a few times per
report run, not in a hot per-trial loop, so the performance delta is
noise) — flagged for completeness since it is a genuine, verified
duplicate, but explicitly not worth more ceremony than the one-line move
above.

### 4. Connects to Area 1: `ffcore/league.py`'s `app_fielded()` (called from `xi.py`) hand-rolls its own id-then-name ladder instead of calling the crosswalk

`xi.py: fielded()` (`xi.py:18-25`) calls `app_fielded(squad, {})`
(`ffcore/league.py:208-229`), which is outside this area's assigned file
list but is the one place a file *in* this area (`xi.py`) hands rows to
an identity-resolution ladder Area 1 didn't enumerate. Reading it:
`app_fielded` builds `ids = {app_id: player_id}` directly from
`xw.players.values()` (`league.py:213-215`) and `by_name = {norm(name):
key}` from the squad (`:217`), then for each API-lineup row does `key =
ids.get(app_id)`, falling back to `by_name.get(norm(player_name))` —
the same "id, then unique name" shape `Crosswalk.player(app_id=...,
app_name=...)` already implements, reimplemented by hand instead of
called. This was not in Area 1's ten-site inventory (that inventory was
built from `crosswalk.py`/`ffcore/crosswalk.py`/`score.py`/
`lineupweight.py`/`second.py`/`startprob.py`/`decide.py`/`slate.py`/
`league.py`'s `_roster_key`, and explicitly did not walk every caller of
every module). **Not the same fix as items 1-9 there**, because
`app_fielded`'s contract is genuinely different in one respect Area 1's
ladder doesn't have: it aborts the whole call (`return []`) on the
*first* row that doesn't resolve into the given `squad` set
(`league.py:226-227`) — a fail-closed verification, not a per-row
best-effort resolve. That abort semantics has to survive any change.
But the per-row lookup inside the loop could still become `xw.player(app_id=...,
app_name=...)` instead of two hand-built dicts, once `xw` is threaded in
(today `app_fielded` takes raw `ids`/`names`, not a crosswalk, for
testability — its own self-test in `xi.py:76-108` calls it with a bare
`Crosswalk`-less signature). **Flagged, not fully specified**: this is a
real, evidence-backed instance of the pattern Area 1 named, found only
because this area's brief asked to check whether Area 1 missed
call sites outside its own inventory — it did, here — but reworking
`app_fielded`'s signature is `league.py`'s call, one file outside this
area's assignment, so it is recorded here as a lead for whoever next
touches `league.py`, not executed as part of this area's patch.

### 5. Everything else in this area's module list: checked per-function, correctly left alone

- **`squads.py`** — `log_slate`, `row`/`flag`, `sec_premium`, `sec_drift`,
  `write_league`, `main` each build one distinct part of the league
  report from data already resolved upstream (`lg`, `players`, `dl`,
  `second`); none calls into crosswalk resolution or duplicates
  `report.py`'s or `flip.py`'s sections. No identity-resolution ladder
  present at all — every lookup here is a dict `.get()` against an
  already-keyed table, confirming the file does no per-row resolving of
  its own for Area 1 to have missed.
- **`scout.py`** — `table()`/`render()`/`_form()`/`_play()` build one
  per-player status table from `decide.load()`, `load_players()`,
  `load_perjornada()`; no duplicate of `squads.py` or `report.py` (it's
  a squad-status view, not a league or alerts view — different columns,
  different source composition, single caller each).
- **`gap_signal.py`** — `_jornada_dates`, `_status_history`,
  `_status_around`, `gap_cases`, `leave_one_out` is a single self-
  contained statistical check (does an unexplained scoring gap predict a
  discount on return); the one crosswalk touch (`_per_jornada_current`)
  is already Area 1 item 1, unchanged here. No new ladder, no shared
  logic with any other module in this list.
- **`report.py`** — `log_squad`, `stale_feed_warnings`, `alerts`, `main`
  build the day's alert list from `sc.score_squad`, `sim.shape`, and
  already-resolved market/ledger data; the formation-recount bug this
  file's own comment (`report.py:132-142`) documents fixing is already
  fixed (calls `sim.shape(u, xi)` rather than re-deriving DEF/MED/DEL
  counts) — re-verified by reading, not re-asserted from the comment
  alone. No overlap found with `squads.py`'s league table or `flip.py`'s
  market view.
- **`flip.py`** — already tightly factored: `_belief`/`_hold_value` are
  explicit, documented shared helpers (`flip.py:193-205`) called by
  `picks`/`sells`/`fund`, exactly the kind of extraction Area 3 asked
  whether `fit_*` needed and found it didn't need there — here it was
  already done. `say()` is confirmed the single place a reason becomes
  a sentence (every `reason["code"]` branch lives in one function,
  `:383-424`); `present()` composes sections from decisions with no
  duplicate wording logic. No changes proposed.
- **`backtest.py`** — beyond item 2 above, the two merges the task
  description flagged as already done (`_checked_golden`/`_mae_result`
  shared by `naive_value_baseline`/`recency_only_baseline`;
  `_pick_episodes`/`_grade_episodes` shared by all three `replay_*`
  paths) are real and correctly done — confirmed by reading both call
  sites of each helper, not by trusting the docstrings that describe
  them as shared.
- **`ffcore/score.py`'s non-fit_* content** (`Scorer.rate`,
  `Scorer.score`, `squad_pool`, `replacement`, `vor`, `build`) — beyond
  item 1 above, `rate()`'s multi-term blend (history, current-season,
  xg, shots) is one function computing one blended estimate, not
  several similar ones; `score()` composes `rate()` with lineup
  percentage and fixture difficulty in a single pass with no
  duplicate of `rate()`'s own weighting logic. `squad_pool` (grouping by
  slot, sorted descending) and `replacement`/`vor` (replacement-level
  and value-over-replacement off that pool) are three small, genuinely
  different one-purpose functions with no overlap with `pick_xi`'s
  search (confirmed: `squad_pool`/`replacement`/`vor` never call
  `_xi_search`, and `pick_xi` never calls `replacement`/`vor` — two
  independent consumers of the same `pool` shape, not one job in two
  places). `build()` is pure orchestration (calls each fitted-value
  loader once, constructs one `Scorer`); no logic of its own to
  duplicate.
- **`ffcore/season.py`** — beyond the `SHAPES` item above, `best_xi`
  correctly reuses `_xi_search` (the actual search, not a re-
  implementation); `simulate_many`/`_run_np`/`simulate` are one Monte
  Carlo engine with a single entry point (`simulate` is a one-state
  wrapper over `simulate_many`, not a second implementation);
  `Standings`' methods (`mean`, `band`, `beat`, `position`,
  `expected_position`) are five distinct statistics over one `totals`
  table, none recomputing another's work redundantly (`expected_position`
  calls `position()` rather than re-deriving ranks, confirmed at
  `season.py:75-76`).
- **`ffcore/profile.py`** — `_match_stats_history`, `_opponent_history`,
  `_perjornada_history` build three distinct per-player histories from
  three distinct row shapes (api_stats, matches, perjornada); no shared
  logic between them beyond the generic "index by ff_id/key" idiom,
  which is a two-line dict-of-dict build each time, the same
  non-finding as squads.py's date-sort idiom above. `build_profiles`
  composes all three plus `Scorer.row_for`/`score` into one
  `PlayerProfile` per key — this *is* a fourth crosswalk-adjacent lookup
  (`xw.players.get(k)`, `:148`) but it's a plain dict get against an
  already-built crosswalk, not a raw-row resolve, so it isn't an
  eleventh site for Area 1's inventory.
- **`ffcore/crosswalk.py`, `src/crosswalk.py`** — both are exhaustively
  covered by Area 1 already (the `Crosswalk.player`/`resolve`/
  `resolve_api` ladder, cited there with the same line numbers this
  read reconfirmed). The build-time functions in `src/crosswalk.py`
  (`build_clubs`, `build_players`, `namesakes`, `group_by_name`,
  `attach_bulk_app_ids`, `build_understat_ids`) are a genuinely separate
  phase — they construct the crosswalk from raw scraped rows *before*
  one exists to resolve against (the same bootstrap-ordering fact Area 1
  already used to explain why "resolve once at read time" can't run
  before the crosswalk stage). `build_players`'s internal `by_name()`
  closure (`:99-104`) does its own shared-name/club-narrowing, but this
  is name matching among *freshly built, not-yet-keyed* `Player` objects
  during construction — a different job from Area 1's "resolve a row to
  an existing canonical key," and confirmed not called from anywhere
  outside `build_players` itself, so it is not a hidden eleventh
  instance of the identity-resolution ladder either.
- **`xi.py`** — beyond the `app_fielded` connection above, `fielded()`
  is the whole contract (classify the app's own lineup feed into
  XI/bench/warnings); `main()` and `_selftest()` add no further logic.
- **`points.py`** — `match_jornadas`/`jornada_asof`/`player_key`/
  `totals`/`keep_changed`/`diff`/`load_snapshots` is one pipeline
  (snapshot -> per-jornada delta), each function a distinct stage with
  no duplicate of another; `player_key`'s "ff_id else norm(full-or-
  short-name)" fallback (`:50-52`) is the same one-rung ladder shape as
  score.py items 3/4 in Area 1 — no name-only fallback is possible here
  either (there's no separate name field to fall back to beyond what's
  already tried), so this is correctly left as a plain function, not
  forced into `Crosswalk.resolve()`.
- **`digest.py`** — `split_sections`/`_key`/`digest` is a single,
  well-contained markdown-splicing contract with no second
  implementation anywhere else in the codebase (confirmed: no other
  module reads `## `-delimited markdown sections this way).

### Net effect if items 1-4 above are executed

Item 1 (Scorer.__init__ helper): -1 function net (adds `_key_of`,
removes ~6 duplicate lines, roughly break-even on function count, -6
lines). Item 2 (backtest.py `_replay_setup`): +1 function, -1 duplicated
setup block, roughly neutral on line count, removes one redundant
`git log`/`_actuals_index()` pair when both replay arms are requested in
the same call. Item 3 (SHAPES): -1 duplicate list literal, +0 net
functions, ~-2 lines. Item 4 (app_fielded) is a lead, not an executed
change in this area — no line delta counted. **Total: roughly neutral to
slightly negative on line and function count** (a handful of lines
removed, one small helper added), consistent with this plan's stated
aim of not chasing a count target — the actual yield here is smaller
than Areas 1-4 found, which is itself the honest result of applying the
same rigor to modules that, on inspection, were already well-factored or
had already had their real duplication caught by this session's earlier
passes.

## Non-goals

- No function-count or line-count target.
- No rewrite of the crosswalk or decide.py's decision engine.
- No change to anything outside Areas 1 and 2 above.
