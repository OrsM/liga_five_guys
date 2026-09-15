# Duplication audit — 2026-09-14

Full-codebase swarm inventory + Phase 2 cross-cutting duplication assessment.
No pre-exemptions applied — every candidate (including sources.py's parse/sign
pairs) was verified against real source, not waved through as intentional
boilerplate.

Ranked worst first. Categories: (1) disagreement-risk duplication — two paths
compute the same real-world fact and can silently disagree; (2) collapsible
repetition; (3) wrong abstraction level; (4) redundant reimplementation of an
existing primitive.

## Real bugs (silently wrong data, not just style)

1. **`sources.py`'s `parse_fitness()` uses a hand-written `_fold()` instead of
   `ffcore.text.norm()`** — they disagree on apostrophe names ("N'Diaye":
   `norm`→"ndiaye", `_fold`→"n diaye"). This key merges the starting-XI and
   injury/suspension blocks into one record per player, so a fold mismatch
   means those two blocks can silently fail to merge. Category 1.
   Fix: delete `_fold`, call `norm()`.

2. **`scout.py`'s `_last_season()`/`_this_season()` duplicate
   `ffcore.score.load_points()`/`methodology.load_actuals()`** with different
   keys (raw `ff_id` vs. crosswalk-normalized name) and different filters (no
   windowing/fallback vs. windowing + stale-file fallback). Can disagree
   today; the hardcoded `"2026-27"` path will silently return `{}` next
   season since the canonical loaders derive the season label dynamically by
   globbing. Currently display-only (`scout.py` has zero external callers —
   doesn't feed decide.py/report.py/sim.py). Category 1 + 4.
   Fix: delete both, have `scout.table()` consume `load_points()`/
   `load_actuals()` directly.

3. **`ingest.py`'s `route()` is dead code** — the canonical "file each row
   under its table, stamp it" function is called only by the self-test;
   `parse()` reimplements the same logic inline instead. The self-test
   exercises the unused twin, giving false coverage. Category 2.
   Fix: call `route(pending, rows, src.table, stamp)` directly in `parse()`,
   drop the duplicate stamping in the finalize loop.

4. **`sign_points` breaks sources.py's own established anti-drift pattern** —
   10 of 17 `sign_X` functions call their own `parse_X` sibling directly
   (structurally can't diverge); `sign_points` instead digests every table on
   the page unfiltered, defeating dedup and violating the module's documented
   signing contract. Category 2/3.
   Fix: rewrite to call `parse_points(html, "")` and digest
   `(ff_id, points, games)` per row, like `sign_odds`/`sign_api_market`.

## Architectural duplication (two systems computing the same real-world fact)

5. **Two independently-built "current XI" pipelines** —
   `report.py`'s `squad_pool()`/`pick_xi()` (via `Scorer.score()`, timed via
   `fixture_board()`) vs `decide.current_xi()` (via `Bootstrap.expected_own()`,
   timed via jornada-index logic; drives `sim.py`'s whole ladder). Both
   bottom out in the shared `_xi_search()` core but are fed from two
   separately-engineered value/timing systems — evidenced by both needing
   their own separate fix for the same round-in-progress edge case. If they
   ever pick different elevens, the report's warning/grading artifact and its
   recommendation artifact silently describe different squads in the same
   run. Same failure shape as the stale-deadline near-incident. Category 1.
   Fix direction: route report.py's "current XI" through
   `decide.load()` + `decide.current_xi(u)` instead of a second, standalone
   `squad_pool()`/`pick_xi()` call.

6. **`methodology.py`'s 5-way duplicate "group claims by key, sort by
   timestamp" builder** — `start_grade()`, `_start_instances()`,
   `_instance_briers()`, `golden_rows()`, `load_predictions()` each
   independently implement the same `setdefault(...).append((when, row))` +
   sort shape (`_start_instances`/`_instance_briers` are near byte-identical).
   Held together only by callers remembering an invariant (pre-filtered
   inputs), not by the code enforcing it. `match_claim()` (already shared,
   added this session) only fixed the *lookup* half of this problem, not the
   *store-construction* half. Category 2 (bordering 1).
   Fix: add `_group_by_key(rows, when_key=, name_key=, src=, universe=)` next
   to `match_claim()`; have all five call it (`load_predictions()` needs a
   `value_fn` param).

7. **`_fd_match_team` (sources.py) is a byte-for-byte copy of
   `ffcore.fixture.match_team`** — deliberate, commented tradeoff for
   dependency isolation, but it's called from 3 live sites and will silently
   diverge if `match_team`'s matching rule is ever fixed. Category 4.
   Fix direction: extract `match_team`'s rule into a genuinely
   dependency-free leaf module instead of hand-copying the algorithm.

8. **`sim.py`'s clause premium "median 1.52× market value" is hardcoded** —
   no live function backs this number; `ffcore.bid.premiums()` already has
   the machinery (used for buy/sell sides) but no "steal"/clause side today.
   Same caption-drift shape as the fixed HOME_EDGE bug. Category 1 (weaker).
   Fix direction: classify clause buys as their own deal side (or via
   `route=="steal"`), extend `premiums()` to report a live median, point the
   caveat text at it.

## Lower-severity / cold-path / cosmetic

9. `decide.current_xi(u)` recomputed ~9× per report render across
   `candidates()`, `band_acts()`, `ladder_rows()`, `ladder()`, `xi_note()`,
   `fielded_shape()`, `_bar()`, `_xi_total()`, `_shape_now()`. Cold path
   (once per report, deterministic, no drift risk) — just wasted work.
   Category 2. Fix: compute `exp, xi = decide.current_xi(u)` once in
   `main()`/`payload()`, thread through as an optional param the way
   `by_slot(u, keys, exp=None)` already does.

10. `ALERTS` path constant (`Path(os.environ.get("LFG_ALERTS",
    ".runtime/alerts.md"))`) duplicated verbatim in `sim.py` and `report.py`
    — both write the same file in one run. No live disagreement today, but
    nothing enforces the two literals staying in sync. Category 2.
    Fix: define once (e.g. in `ffcore/tidy.py`), import into both.

11. PAR captions hand-written separately in `slate.comparison_table()` and
    `methodology.column_guide_lines()` — currently describe the same concept
    correctly, but unlinked (no shared source), same risk class as the
    already-fixed ladder-column-guide/Fantasy.jsx drift. Category 1 (weak)/2.
    Fix: have `slate.py`'s caption quote/pull from `methodology.py`'s.

12. `sim.decide_dead(u)` — confirmed dead one-line wrapper around
    `decide.dead_weight(u)`, called twice in `ladder_rows()` (not once as
    originally assumed) — second call redoes a `best_xi()` search for no
    benefit. Category 2. Fix: call once, reuse result; consider inlining the
    wrapper.

13. `crosswalk.py`'s `by_name` closure (inside `build_players()`) vs
    `Market._pick()` (tidy.py) — same "narrow a shared name by club" algorithm
    written twice over genuinely different populations (full market history
    vs. latest snapshot — not collapsible outright) but the club-narrowing
    sub-step itself could be a shared helper. Category 3.
    Fix direction: extract `narrow_by_club(candidates, want_club, club_of_fn)`
    into `ffcore/text.py` or `ffcore/crosswalk.py`.

14. Player fitness/status answered by three unreconciled sources
    (`parse_team`'s futbolfantasy status, `parse_af_team`'s always-blank
    status, `parse_api_teams`'s `player_status`) — deliberately kept
    separate (documented), no reconciliation step anywhere. Category 1
    by design — flagged for whoever owns the decision-engine's read of these
    columns, not a code fix here.

15. Four `*_source(key)` functions (`match_source`, `api_source`,
    `player_source`, `offer_source`) independently rebuild a `Source` from
    its key. Could be one data-driven dispatch table
    (`{regex/prefix → (table, parse, sign, cadence, auth)}`), but
    `api_source`'s multi-key-per-URL variation means the collapse isn't
    free. Category 2, low priority.

16. `ffcore/text.py`'s `__all__` lists `fold` but no `fold` function exists
    in the file — dead/stale export, likely a rename artifact (`norm()`'s
    docstring describes what `fold` probably used to be called). Harmless
    today (nothing imports it). One-line fix: delete from `__all__`.

## Also noted, not ranked (consistency nits, no live risk)

- `sign_calendar`/`sign_af_fixtures`/`sign_points` bypass the module's `_css()`
  memoized selector cache, unlike every other `sign_X`. Low impact (single
  low-volume pages, not the 40-team/380-match sweep `_css()` was built for).
- `methodology.py`'s ad hoc `_XW_CACHE` mirrors a caching problem
  (`load_crosswalk()` has no built-in cache) that `league.py` already solved
  once for its own use case — not a correctness issue, just a caching
  decision made locally instead of centrally.

## Status

Plan is to fix items 1, 3, 4, 7, 10, 16 first (small, contained, obvious
correct fix, no design ambiguity). Items 2, 5, 6, 8, 11, 13 touch report
semantics or need a shared-helper design decision — hold for explicit
go-ahead before touching, since they're more invasive than a local cleanup.
