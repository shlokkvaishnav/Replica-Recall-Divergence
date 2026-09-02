# Spec: analysis/cross-system-claim-surface

**Branch:** `analysis/cross-system-claim-surface`
**Date opened:** 2026-09-02
**Status:** COMPLETE — outcome (a). See Results, below.

Issue: closes #15. Body copied verbatim below (per `research/AGENT_PIPELINE.md`'s
implementer instructions — the issue text unmodified, heading levels normalized).

**Process note, recorded rather than hidden:** this file was committed *after* the
implementation, not before it, breaking `GIT_WORKFLOW.md`'s spec-first rule. The
rule's purpose — timestamping a hypothesis so it can't be written after seeing the
result — is not materially at stake for a docs correction whose "result" is a diff
and whose evidence (PR #11's committed telemetry) was fixed before this branch
existed. But "the rule didn't matter this time" is a judgement the reviewer should
get to make, not one to make silently by omission. The issue body below is
unmodified from the version filed before any of this branch's edits, and that
filing *is* timestamped on #15.

---

## Type

analysis (analysis of an existing result, no new data collection)

## Research question

Does the project's cross-system claim surface still match the cross-system evidence, now that PR #11 has shown that the `index_recall` null behind it was measured over a mostly- or entirely-unindexed corpus? Concretely: what should `research/cross_system_replication/SPEC.md`'s Interpretation section say, given that its "the divergence is concentrated in data completeness, not graph quality, unlike nano-db" was PR #6's headline cross-system finding and PR #11 has since shown that at those sweep parameters, `index_recall` was largely not measuring a graph at all?

## Hypothesis

The claim needs to be materially weakened, not merely footnoted. PR #11 established, across two instrumented seeds, that at PR #6's own sweep scale (100k vectors, 128-dim, 90–150s runs) Qdrant's collection reported `indexed_vectors_count = 0` on every node for the entire baseline period and for most (seed 20260920) or all (seed 20260921) of the chaos window; 60% and 84% of scorable `index_recall` samples respectively were taken with nothing indexed anywhere. Searches served from flat, unindexed segments are exact by construction, so a near-1.0 `index_recall` in that window is mechanically expected regardless of chaos. If that is right, "Qdrant's graph quality doesn't diverge under chaos" is not a finding about Qdrant's replication at all — it is an artifact of the measurement window, and the honest statement is that the comparison was not made.

## Null / alternative hypothesis

Null: the existing wording is already adequately hedged — `SPEC.md:123` does say the *why* is unchecked and names optimizer masking as an open confound (Decision item 4), so a reader following the document as written would not walk away with an unsupported architectural claim, and no correction beyond a pointer to PR #11 is warranted. Alternative (as hypothesized): "the divergence is concentrated in data completeness, not graph quality, unlike nano-db" is stated in the **Establishes** half of that section's establishes/does-not-establish split, where the hedge does not reach it — the caveat sits in the *does not establish* half and names a different, weaker confound (post-hoc merge repair) than the one PR #11 actually found (no graph in use during measurement). A caveat that names the wrong mechanism does not cover the right one.

## Motivation

This is PR #11's own Decision item 2, deliberately left unwritten there ("this addendum reports the second run's evidence, it does not decide what to do with it. That's for review") and still open after that PR merged. It is the highest-stakes stale claim currently in the repository, for three reasons: it sits on the project's **top-priority** open research question (cross-system generalization — the step the top-level README calls "what would turn a measurement of one system into a contribution about the field"); it is a claim about a *second system*, which is exactly the kind of statement the DO-NOT-CLAIM section exists to police; and it is currently stated as an establishment rather than a hypothesis. `DECISION_LOG.md` already records two corrections of this exact shape (the ClickHouse citation removal, the ground-truth-free → judge-free walk-back), both handled by correcting rather than softening — this is the same call, on a larger claim.

Two related staleness items on the same claim surface, in scope for the same reason:

1. `research/README.md`'s experiment index still lists cross-system replication as "**Not started** — spec committed, no implementation or results yet," which two merged PRs (#6, #11) contradict. The index is the first thing a reader consults to find out what exists.
2. The top-level README's open question #1 still frames cross-system work as an untaken next step, with no mention that a Qdrant sweep exists and what it did and did not show.

Both are the same underlying defect as the main question — the cross-system claim surface not matching the cross-system evidence — which is why they belong in this issue rather than three.

## Experimental design

No new experiment, no new data. Read, against each other: (a) `research/qdrant_optimizer_masking/SPEC.md`'s Results, Interpretation and both Decision sections, plus its two committed telemetry runs and `analyze_indexing_lag.py`'s output; (b) `research/cross_system_replication/SPEC.md`'s Interpretation section, particularly the establishes/does-not-establish split at line ~123 and Decision item 4; (c) `research/README.md`'s experiment index row; (d) the top-level `README.md`'s open question #1 and its DO-NOT-CLAIM section. Produce a minimal, reviewable diff that moves the `index_recall` statement out of "established" into an explicit "not measured, and here is why," updates the index row to reflect what actually exists, and checks whether DO-NOT-CLAIM needs a line about the Qdrant `index_recall` null specifically.

Explicitly out of scope: re-running anything, changing the harness, or writing the protocol fix (front-loading indexing / lowering `indexing_threshold`) that PR #11's Decision item 4 calls for — that is a `method/*` change and a separate issue.

## Metrics

Not statistical. The outcome is a diff that a subsequent review can check line-by-line against PR #11's committed telemetry, in the same way #10's README change was checked against PR #7's `compare_conditions_output.txt`. The specific check a reviewer should be able to make: every surviving sentence about Qdrant's `index_recall` should be one that stays true given 60% and 84% of samples were taken with zero vectors indexed.

## Baselines / controls

PR #11's committed telemetry (`research/qdrant_optimizer_masking/results/`) and its two-seed table are the ground truth the new wording must match. The existing scope caveats already in place — single fault model, SIGKILL only, one topology, n=5 — carry forward unchanged rather than being loosened on the way past. The correction must not overshoot either: PR #11 explicitly does **not** establish that Qdrant's graph *would* diverge once fully indexed, so the corrected text must not imply a positive finding where there is now simply no measurement.

## Expected outcomes

(a) The establishes/does-not-establish split needs the `index_recall` clause moved across the line, plus a short statement of the mechanism and a pointer to PR #11 — the likely common case, matching how #10 resolved. (b) Moving it exposes that PR #6's *other* two findings (spread separates cleanly; healing is seed-inconsistent) also depend on the same measurement window and need their own scope check — a larger task than a wording fix, and worth splitting out if so rather than doing quietly. (c) The null holds: the existing hedge is judged sufficient, no change is made, and that is recorded explicitly with reasoning rather than left as a silent decision.

## Interpretation plan

(a) and (b) both produce a diff plus a `DECISION_LOG.md` entry, per that document's stated policy — a claim's scope changing on new evidence is exactly its brief, and the two prior corrections of this shape are the precedent for how it should read. (b) additionally files a follow-on issue rather than expanding this one. (c) closes as ABANDON-with-reason so a later pass doesn't re-ask. In all three cases, PR #6 and PR #11 should get a comment linking the outcome, since both are merged and neither can be amended.

## Confounds considered

**Overcorrection risk, and it is the main one here.** The temptation is to read PR #11 as "Qdrant's `index_recall` result was wrong," when what it shows is that the result was *not measured*. Those need different words, and the second is the accurate one: no positive claim about Qdrant's graph quality under chaos is licensed in either direction. **Scope creep:** the cross-system SPEC's healing and spread findings are adjacent and tempting to revisit; they belong in outcome (b)'s follow-on unless the reading forces the issue. **Authority risk:** PR #11's telemetry finding is itself two runs at one scale, and its own text says the exact timing is "this run's own number, not a universal constant" — the correction should not import more certainty from it than it claims for itself. **Self-consistency:** the corrected text will sit alongside the DO-NOT-CLAIM line that already says generalization to Qdrant is untested; check the two now agree, since a stronger correction here may make that existing line read as redundant or, worse, as contradicting a claim elsewhere that a Qdrant sweep established something.


---

## Results

The three claim sites the issue named, and what each needed:

1. **`research/cross_system_replication/SPEC.md`'s establishes/does-not-establish
   split** — "the divergence is concentrated in data completeness, not graph
   quality, unlike nano-db" sat on the *establishes* side. Struck through in
   place, with the reason and a pointer; the does-not side now says plainly that
   neither "Qdrant's graph is unaffected" nor "Qdrant differs from nano-db here"
   is established. A warning pointer was added at the headline-finding paragraph
   above it, since that paragraph is where a reader meets the claim first.
2. **`research/README.md`'s experiment index** — said cross-system replication was
   "**Not started** — spec committed, no implementation or results yet" after two
   merged PRs. Now states what exists and what it did and did not show, with a
   second row for `qdrant_optimizer_masking/`. The stale "open branch" pointer
   below the table is relabelled as superseded rather than deleted.
3. **Top-level `README.md`** — open question #1 rewritten from "point the protocol
   at a second database" (done) to the two sharper remaining steps: re-measure
   Qdrant's `index_recall` with indexing front-loaded, and point the protocol at a
   system with real anti-entropy. A DO-NOT-CLAIM line was added for the withdrawn
   claim, in both directions.

Plus a full correction addendum at the end of the cross-system spec, carrying PR
#11's two-seed telemetry table as the evidence.

**Confirmed unaffected, and deliberately not touched:** `completeness`,
`e2e_recall`, within-shard spread, and the healing results. None involve the HNSW
graph — `completeness` involves no search at all — so indexing state does not
bear on them. This was checked, not assumed; it is also the issue's outcome (b)
trigger, and it did not fire.

## Interpretation

Outcome **(a)**: the claim moved across the establishes/does-not line, with the
mechanism stated and PR #11 cited. Outcome (b) did not fire.

The judgement that shaped every sentence: **this is an absent measurement, not a
null result**, and those need different words. The available overcorrection —
"Qdrant's graph does degrade under chaos, we just measured it wrong" — is not
supported by anything: PR #11's own runs never finished indexing either, so they
cannot speak to what a fully-indexed Qdrant would show. Withdrawing in both
directions is the only reading the evidence licenses, and it is a weaker, less
satisfying claim than either the original or its reversal.

Struck through rather than deleted, per `GIT_WORKFLOW.md`. The transferable
lesson is not the corrected sentence but the failure mode: a metric can quietly
measure something other than what it is defined to measure when an
un-instrumented system behaviour — here, indexing lag at this write rate and
scale — changes what the query engine is actually doing underneath it. Nothing in
the harness was wrong; `index_recall` computed exactly what it always computes,
over a backend that had silently stopped being approximate.

## Decision

**MERGE** (self-assessed; the reviewer decides independently). Docs-only. Every
figure traces to PR #11's committed telemetry. The correction is bounded to the
one claim the evidence reaches, the adjacent findings were checked rather than
assumed unaffected, and the protocol fix PR #11's Decision item 4 calls for is
left to its own `method/*` branch rather than folded in.
