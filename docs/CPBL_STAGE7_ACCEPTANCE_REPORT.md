# CPBL Stage 7 Engineering Acceptance Report

Status: **COMPLETE**

Branch: `feature/cpbl-dataset`

Acceptance date: 2026-09-15

Product-code acceptance commit: `eb026f63829ccbad7f579df10d7d38452c04380e`

This report closes the engineering/data portion of Stage 7. The final acceptance scope was deliberately reduced to checks that can reveal user-visible data loss, nondeterministic recovery, regressions, or broken analysis behavior. Previously completed full-data parity, performance, backend, and browser acceptance were not redundantly rerun after the final raw-rebuild-only fix.

## Full-season acquisition and reconciliation

Test window: `2026-01-01` through `2026-09-15`.

Latest clean acquisition/reconciliation evidence:

- schedule games: **572**
- archived raw game payloads: **572**
- schedule occurrences indicating actual play: **537**
- normalized games: **537**
- ingest-eligible identifiable pitches: **165,089**
- normalized pitches: **165,089**
- Trackman-measured pitches: **108,965**
- identifiable pitches retained without Trackman measurements: **56,124**
- duplicate `pitch_uid` groups: **0**
- raw-vs-normalized reconciliation mismatches: **0**
- scheduled games missing a raw payload: **0**
- normalized rows beyond the acquisition window: **0**

The earlier apparent mismatch for `2026-D-208` was an acceptance-oracle defect, not lost data. The 2026-08-19 schedule occurrence is explicitly `POSTPONED`; CPBL can nevertheless return a populated game-detail payload for the same GameId. Live ingestion intentionally archives that detail without ingesting its LiveLog. The Stage 7 audit now uses the same schedule-play predicate as ingestion, so explicit non-play states are expected to normalize to zero rows unless the same GameId has another archived schedule occurrence showing actual play.

## Raw rebuild and idempotency

The focused final acceptance rebuilt a fresh SQLite database from the archived raw corpus and compared it with the clean acquired database.

Before rebuild:

- games: **537**
- pitch rows: **165,089**
- pitch-row fingerprint: `06861f510f4917c8fb449fc3edd2174c7c165cfe8d70e18e48b6f2eaf14da196`

After raw rebuild:

- games: **537**
- pitch rows: **165,089**
- pitch-row fingerprint: `06861f510f4917c8fb449fc3edd2174c7c165cfe8d70e18e48b6f2eaf14da196`

Result: **PASS — rebuilt analytical state is identical.**

A real rebuild bug was found during this acceptance: `rebuild_from_raw()` previously replayed archived detail payloads for GameIds whose schedule history contained only explicit non-play states, while live backfill skipped them. The rebuild path now derives non-play-only GameIds from archived schedules and applies the same source contract. A GameId that later has `RESERVED`, `START`, `FINISHED`, or another play-indicating state remains ingestible.

Additional lifecycle evidence from the same focused run:

- raw snapshot integrity verification passed;
- deliberate raw corruption was detected;
- a repeated normal ingest of a stable date produced **0 inserted / 0 updated** rows on the second pass;
- interruption/resume recovery completed successfully;
- a second full raw replay converged to the same final database state. Because the raw archive contains historical snapshots of the same games, replaying all snapshots can report transient row updates while progressing from older to newer snapshots; this does not change the final fingerprint and is treated as non-blocking internal replay behavior.

## Analysis and product acceptance already completed

Before the final focused closure, Stage 7 had already exercised the full CPBL dataset through the shared analysis stack:

- full-data SQLite ↔ DuckDB parity: **10/10 representative mode families passed**;
- the `FollowEvent` full-data SQLite plan was optimized from a >30-minute timeout to roughly 95 seconds while retaining SQLite/DuckDB result equality;
- full-data performance/cache lifecycle passed;
- ten real-data backend acceptance questions passed;
- the same ten CPBL workspace journeys passed through Chromium E2E after correcting a Playwright test-harness compatibility issue.

These were not rerun after the final rebuild fix because `eb026f6` changes only CPBL raw recovery/non-play-state handling and does not alter the analysis compiler, web workspace, or already-acquired normalized dataset.

## Regression status

Normal CI on product-code commit `eb026f63829ccbad7f579df10d7d38452c04380e` completed successfully, covering the existing non-integration suite and live integration smoke. The existing MLB behavior therefore remains covered by the shared regression suite after the CPBL sync/rebuild changes.

## Final disposition

**Stage 1–7 engineering implementation/acceptance is complete.**

No additional engineering acceptance gate is required for this CPBL feature before review/merge. Remaining checks, if desired, are human/product judgments only: visual UI preference, wording, and manual spot-checks against selected CPBL pages. Those are not engineering blockers.
