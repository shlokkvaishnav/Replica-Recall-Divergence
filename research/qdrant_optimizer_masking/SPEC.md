# Spec: experiment/qdrant-optimizer-masking-index-recall

**Branch:** `experiment/qdrant-optimizer-masking-index-recall`
**Date opened:** 2026-08-24
**Status:** DRAFT — no implementation or results yet. This file is committed before either exists.

Issue: closes #8. Body copied verbatim below (per `research/AGENT_PIPELINE.md`'s implementer instructions — this is the issue text unmodified, not a paraphrase).

## Research question

On the merged Qdrant cross-system-replication result (PR #6), the pre-registered 5-seed sweep found `completeness`/`e2e_recall` separate cleanly between baseline and chaos (p=0.0079 each) but `index_recall` does not (p=1.0000, means 0.9920 vs 0.9918) — the opposite pattern from nano-db's own headline result, where `index_recall` does separate. Is Qdrant's own background segment-merge/optimizer activity masking a real graph-quality (`index_recall`) divergence during the probe windows, or is the null a genuine absence of graph-level divergence on this system?

## Hypothesis

Qdrant's optimizer runs segment merges and re-indexing in the background independent of the replication/chaos protocol under test, and if a merge happens to complete during (or shortly before) a probe window, it could locally repair or mask graph-quality damage that would otherwise show up as an `index_recall` gap — meaning the current null result may reflect probe timing relative to optimizer activity rather than a true absence of a Qdrant-side analog to nano-db's graph-degradation mechanism.

## Null / alternative hypothesis

Null: optimizer/segment-merge events (captured from Qdrant's own logs/telemetry during a fresh instrumented run) show no temporal correlation with `index_recall` sample values or with the timing of chaos events — the `index_recall` null from PR #6's sweep stands as a genuine absence of graph-quality divergence on Qdrant under this fault model, not an artifact of probe/merge timing. Alternative: `index_recall` samples taken shortly after a logged segment-merge/optimize event are measurably higher (closer to baseline) than samples taken without a recent merge, i.e. the existing null is confounded by asynchronous background repair the current protocol doesn't control for.

## Motivation

This is the first of two follow-on items the reviewer explicitly flagged on merged PR #6 as needing to not "quietly vanish" — SPEC.md's own Confounds section for the cross-system-replication branch named this exact possibility, and the reviewer's final re-review said it should currently be read as "no divergence we could detect at this probe cadence," not a settled mechanism-level claim. Left unresolved, the top-level README/RELATED_WORK framing risk this project already tracks in `research/RELATED_WORK.md` §Framing risks (overclaiming what a null result establishes) applies directly here: an unqualified "Qdrant shows no index_recall divergence" claim would be exactly the kind of statement the project's own DO-NOT-CLAIM discipline exists to prevent until this confound is checked.

## Experimental design

System: same Qdrant 3-node cluster / topology used in `research/cross_system_replication/` (the merged branch), same node-kill chaos protocol, same corpus scale as the pre-registered sweep. New instrumentation only: capture Qdrant's per-node logs (or the metrics/telemetry endpoint, whichever exposes segment-merge/optimizer events with timestamps) throughout at least one fresh baseline+chaos+quiesce run pair, alongside the existing probe protocol unchanged. This is new data collection specifically because the prior sweep's `results_sweep/` runs did not capture node logs — the reviewer noted this explicitly ("this one needs node logs captured during a fresh run, not analysis of what's already collected").

## Metrics

Primary: temporal correlation between logged optimizer/segment-merge completion events and per-replica `index_recall` sample values in the same run — e.g. compare `index_recall` immediately following a logged merge event against `index_recall` samples with no recent merge event, within the same run. Secondary: whether merge-event frequency/timing differs systematically between baseline and chaos conditions (if merges are triggered more/less often under chaos, that itself is relevant context for interpreting the null).

## Baselines / controls

The existing PR #6 5-seed sweep's `index_recall` null result is the reference point this experiment is trying to explain, not re-derive — no new baseline/chaos comparison is being re-run for its own sake. If feasible without excessive additional runs, comparing `index_recall` timing-vs-merge correlation across at least 2 fresh runs (not just 1) would guard against a single run's incidental merge timing being over-interpreted, but a single well-instrumented run is an acceptable minimum given this is fundamentally a mechanism check, not a re-test of the pre-registered comparison.

## Expected outcomes

(a) No correlation found — merges are infrequent or don't align with probe timing, and the `index_recall` null stands as a genuine finding, not an artifact. (b) Some correlation found, but weak/inconsistent — suggests the null is *partly* explained by masking but isn't fully attributable to it, which would need the caveat narrowed rather than either confirmed or retracted. (c) Strong correlation found — the `index_recall` null is substantially confounded by optimizer masking, meaning the "no graph-quality divergence on Qdrant" framing needs to be walked back to something like "no divergence detectable without controlling for background repair," a materially different and weaker claim. (d) Qdrant's telemetry/logs don't expose merge events with enough granularity to test this at all — in which case the confound stays open and undecidable with this system's observability, which is itself worth recording rather than silently dropping the question.

## Interpretation plan

(a) would let the existing PR #6 finding stand as currently framed, closing this specific open confound. (b) would mean the README/RELATED_WORK framing for the Qdrant `index_recall` null needs a precise, narrower caveat (something between "no divergence" and "divergence masked") rather than either extreme — not a full retraction, but not a clean null either. (c) would mean the current cross-system comparison's headline contrast (nano-db: `index_recall` divergence; Qdrant: none) needs to be re-stated as "Qdrant's background repair may explain the difference" rather than presented as a clean architectural distinction between the two systems — a meaningfully different, weaker claim that should be reflected in the top-level README if it changes the thesis. (d) would mean this confound has to be documented as permanently open given available observability, rather than something a future experiment can close — worth noting in DECISION_LOG.md as a limitation rather than leaving silently unaddressed.

## Confounds considered

Instrumentation overhead: enabling verbose logging/telemetry on Qdrant nodes could itself perturb timing (e.g. slow down the node enough to change chaos-recovery dynamics), which would need to be checked or at least noted rather than assumed negligible. Correlation-vs-causation: even a clean temporal correlation between merge events and higher `index_recall` doesn't prove the merge caused the improvement — both could be driven by a third factor (e.g. write-rate slowdown during merges reducing new-insertion-driven graph churn) — the interpretation plan above should stay to "correlated with" language, not "caused by," unless a more targeted intervention (e.g. deliberately triggering/suppressing a merge) is run. Single-run generalization: per Baselines/controls above, drawing a strong conclusion from exactly one instrumented run risks over-interpreting incidental timing; report this as preliminary if only one run is completed within this issue's scope.

---

## Results

*(Not yet — no implementation exists on this branch. This section stays empty until an experiment actually runs.)*

## Interpretation

*(Not yet.)*

## Decision

*(Not yet — DRAFT until results exist. See `GIT_WORKFLOW.md`'s merge criteria before deciding.)*
