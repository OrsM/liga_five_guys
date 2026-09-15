# sources.py — design notes

Long-form "why this source, why this shape" rationale moved out of the
module's section-header comments 2026-09-15 (comment-volume cleanup),
following the same pattern methodology.md/decide.md already use — the
source carries a one-line pointer, this file carries the backstory.

## fitness-classes-lie-in-the-markup

`elemento lesionado elemento_jugador` is the generic class on every pitch-
graphic tile (e.g. `jugadores-titulares-22421 mod lesionados`) — reading
it as an injury marker flags the whole squad. The real signal is the
"Estado físico de la plantilla" panel's icon alt text:

    .lesionados_wrapper section.mod.lesionados > .elemento   alt=Lesionado
                                                              alt=Duda
                                                              alt=Tocado

Suspensions live in `section.mod.sancionados`, but that class is also
used by an unrelated transfer-listing box (`.mercado-box`) — must be
excluded. `Tocado` (knock, still listed as available) folds into
`doubt`: both call for the same "think twice before fielding him"
decision.

## parser_sig-cache-key

`ingest` caches parsed rows per document, keyed on the page content.
Keying the invalidation on "this whole file changed" would re-parse
every source whenever any one parser (or fixture) did. Instead a
document's cache key carries the fingerprint of just the parser it
needs: that function's source plus every top-level name it references,
transitively (helpers, constants, regexes). This module imports nothing
from the repo, so that closure is the whole of what a parse can depend
on. A name `parser_sig` can't find falls back to hashing the whole file.

## analitica-fantasy-two-page-shapes

Two page shapes, depending on how close the next match is. Try
Titulares, fall back to Consenso; neither means rot, which is the
correct outcome.

1. Imminent: `<ul aria-label="Titulares <Team>">` — their final call,
   binary, no percentage on the page. `start_pct` stays EMPTY and `note`
   says "titular". Never write 100: a confident editorial call is not a
   stated probability and the report must tell them apart.
2. Further out: `aria-label="Consenso de alineaciones"` — three editors
   split into Unánimes and Más divididos ("Aitor Paredes2/3 titular").
   That fraction is a probability THEY published, so it lands in
   `start_pct` as 100·n/d with the raw fraction kept in `note`.

No fitness on either shape, so `status` is `""` (not stated), never
`"ok"` — silence must not be stored as a clean bill of health. Their
position codes are dropped: the app's positions are the ones the scorer
uses.

## starters-ground-truth-for-grading-probable-xi

Ground truth for grading both probable-XI sources: the calendar says
which matches were played; each played match page carries the confirmed
elevens. This site, not FBref: outcome rows carry the same
`/jugadores/<slug>` ids as the probable-XI pages, so the join needs no
name resolution — and FBref sits behind a Cloudflare challenge while
this site's robots.txt allows all.

Page shape: `.stats-local`/`.stats-visitante`, each one
`table.tablestats` whose tbody alternates a player row
(`tr.plegado.plegable`, name in `td.name`) with a detail row
(`tr.desglose`, the only link to the player). A `tr.header "Suplentes"`
splits eleven from bench. An unplayed match has no table at all — the
calendar's score is the fetch gate. Fetched once ever: a confirmed
eleven never changes after kickoff.

## club-elo-strength-not-price

Squad value is a poor strength proxy (a promoted side that spends isn't
thereby good; one signing moves the whole total). Elo is fitted on
results, free, published daily. Not an HTML table: the country page
embeds its ranking chart as a Vega-Lite spec, so clubs are records in
that spec's `datasets` — a structured read, not a scrape of the
rendered table, so a moved column can't silently become a rating and a
renamed key yields nothing (the rot signal). Robots: clubelo.com allows
all, one request a day.

The CSV API is dead (`api.clubelo.com` resolves but answers on neither
port) — the site moved host with no CSV endpoint, so this reads the
country page instead. `load_elo()` refuses a reading older than the
cadence allows, since a failed fetch otherwise leaves stale rows that
still join.

## football-data-co-uk-real-results

Neither squad value nor Club Elo (one scalar) can split a clean sheet
(opponent-attack-driven) from a goal (opponent-defense-driven).
football-data.co.uk publishes real results — goals, shots, shots on
target, corners — per season since 1993-94 as one flat CSV, free, no
scraping fragility. The current season's file also carries HxG/AxG
(expected goals); not backfilled onto completed seasons. Team-level,
twenty names: the join is twenty club spellings against this repo's own
twenty slugs, same pattern as `ELO_ALIASES` — `match_team()` first, a
named alias only when that ordinary join fails.

## understat-player-level-xg

Player-level xG/xA — the "skill" side of what raw points can't separate
from luck: xG scores the chance, not whether it went in. Team-level xG
already exists (football-data.co.uk, above) for fixture strength; this
is the same idea at player grain. The documented "playersData in a
`<script>` tag" scrape pattern no longer exists on the page — the real
endpoint (confirmed live) is `POST main/getPlayersStats/` with
`{league, season}` form data; GET on the same URL errors. This is the
one source that needs `Source.body`.

## the-leagues-own-api

Everything else in this file is a public page read anonymously. The
`api_*` sources are LaLiga's own endpoints, behind the token
`ffcore/auth.py` holds, and they carry what no scrape could: the live
market (including players managers have listed), every transaction, and
the balances — worth a credential because the hand-typed alternatives
went stale (see `ledger.py`). They return JSON, not HTML; `parse`/`sign`
take text either way, as Club Elo's CSV does, so the registry needs no
new concept. Terms: the app's own API, read with the account's own
credential for its own league. No robots.txt applies, but the same
restraint does — ask once a day, cache, never poll.
