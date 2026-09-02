# Spec: experiment/qdrant-kill-spacing

**Branch:** `experiment/qdrant-kill-spacing`
**Date opened:** 2026-09-02
**Status:** DRAFT - committed before any run of this experiment exists. No
results, no analysis. The amendments below are written now, against evidence
that already existed, precisely so they cannot be chosen after seeing an
outcome.

Issue: closes #9. Body copied verbatim below (heading levels normalized).

Spec-first was broken twice earlier in this session (#15, #17) and disclosed
both times. This file is committed before a single run of this experiment,
which is the point of the rule: the two amendments below both narrow what the
experiment can claim, and neither is checkable against a result yet.

---

## Type

experiment (one specific, narrowly-scoped experiment)

## Research question

The merged Qdrant cross-system-replication sweep (PR #6) found highly variable post-chaos healing outcomes across its 5 seeds (recovery: 84%, 0%*, 25%, -32%, 100% — *a small-numerator floor case), and a follow-up analysis of the already-collected event logs (`analyze_healing_variance.py`) found the worst outcome correlated with the shortest same-node-repeated-kill gap and highest write-failure rate, but explicitly could not rank the other seeds cleanly or rule out alternatives (the reviewer's own words: "narrowed, not confirmed"). Does deliberately controlling kill spacing and kill-target repetition as independent variables (rather than leaving them to incidental chaos-loop randomization) produce a reproducible healing-outcome effect?

## Hypothesis

Repeated kills to the same node within a short recovery window (before that node has caught up on missed writes from the prior kill) drive worse healing outcomes than kills spread across different nodes or spaced further apart, because the node accumulates a growing backlog rather than ever reaching a settled, repair-eligible state — this was the leading (but unconfirmed) explanation the healing-variance analysis on PR #6 surfaced for its worst-performing seed (20260913: shortest same-node recovery gap, by far the highest write-failure rate, only seed that got *worse* after chaos stopped).

## Null / alternative hypothesis

Null: healing outcome (post-chaos missing-write recovery, matching PR #6's absolute-count-based healing metric, not the completeness ratio — per the existing project convention against the "dilution trap") shows no reproducible dependence on kill spacing or same-node-repeat rate when these are deliberately varied as controlled independent variables across seeds — the pattern seen in the incidental 5-seed sweep was coincidental, not causal. Alternative: seeds with short same-node recovery gaps (kills to the same node before it settles) show measurably and reproducibily worse healing outcomes than seeds with long gaps or no repeated same-node kills, holding total kill count roughly constant across conditions.

## Motivation

This is the second of two follow-on items the reviewer flagged as needing to not disappear after PR #6 merged — appropriately scoped there as "new work, not a blocker for this PR's own claims." The existing healing-variance analysis is honest about its own limits (`20260912` had no repeated node at all and still only recovered 25%, and write-failure rate alone doesn't separate the fully-healed seed from the 25%-healed one), so the leading hypothesis is currently unconfirmed, not established — this experiment is what would actually confirm or rule it out, rather than leaving a plausible-sounding but unverified mechanism in the record indefinitely. It also bears on the top-level README's Open research question #2-adjacent territory (root-cause understanding of degradation/healing mechanisms) for the cross-system side of the project specifically.

## Experimental design

System: same Qdrant cluster/topology as `research/cross_system_replication/`. Rather than the existing chaos loop's randomized kill timing/targeting, run a modified chaos harness that deliberately varies exactly two independent variables while holding total kill count and overall chaos-window duration roughly constant: (1) same-node-repeat spacing — the time gap between two kills to the same node, as a controlled short/long condition — and (2) kill targeting — same-node-repeated vs. spread-across-nodes, as a controlled condition. A small factorial design (e.g. short-gap-same-node, long-gap-same-node, spread-across-nodes, each run across multiple seeds) replaces the current incidental-randomization approach for this specific follow-up, while everything else (corpus, settling window, probe protocol, quiesce window) stays identical to the established protocol.

## Metrics

Primary: absolute missing-write recovery count/percentage during the post-chaos quiesce window (matching the existing project convention of judging healing on absolute count, not the completeness ratio, per `DECISION_LOG.md`'s "dilution trap" entry), compared across the controlled kill-spacing/targeting conditions. Secondary: write-failure rate during chaos, to check whether it tracks kill-spacing condition directly (as seed 20260913 suggested) or varies independently of it.

## Baselines / controls

The existing incidental 5-seed sweep from PR #6 serves as the motivating observation, not a statistical baseline for this experiment — this experiment needs its own within-design comparison across the controlled conditions (e.g. short-gap-same-node vs. spread-across-nodes), each replicated across enough seeds to support a real statistical comparison rather than repeating the n=1-per-condition problem the original incidental sweep had.

## Expected outcomes

(a) Short same-node-repeat spacing reproducibly predicts worse healing than long spacing or spread targeting — the hypothesized mechanism is confirmed, and it should be written up as an actual finding (not "narrowed, not confirmed") with the appropriate caveats about Qdrant specifically. (b) No reproducible effect of kill spacing/targeting is found — the original correlation in the incidental sweep was coincidental, and the healing-variance question stays genuinely unexplained; this is a legitimate negative result, not a failure, and should be recorded as such. (c) A different pattern emerges (e.g. total kill count matters more than spacing, or write-failure rate is the real driver and spacing is only a proxy for it) — a partial confirmation that redirects the mechanism hypothesis rather than confirming or refuting the original one cleanly. (d) Effect is present but small/noisy at the seed counts practically achievable, requiring the interpretation to stay tentative rather than either confirmed or ruled out.

## Interpretation plan

(a) would upgrade the current "narrowed, not confirmed" healing-variance finding to an actual, reproducible mechanism claim for Qdrant specifically, and should be recorded as a real finding in the top-level README/RELATED_WORK if the project's scope extends to cross-system mechanism claims (it would still be scoped to Qdrant, not to graph-ANN systems generally, per the project's existing single-system-generalization discipline). (b) would mean the healing-variance question stays open and should be documented as such in DECISION_LOG.md rather than left as an implied-but-unconfirmed explanation floating in PR #6's history. (c) would redirect future healing-variance work toward the alternative driver identified, and should be written up with the same honesty about what it does and doesn't establish that the original healing-variance analysis already modeled. (d) would mean this specific question may need a larger seed budget than is practical right now, and should be recorded as inconclusive-at-current-scale rather than forced into a directional read.

## Confounds considered

Total kill count and overall chaos duration must be held roughly constant across the controlled conditions, or an observed spacing effect could really be a total-disruption-amount effect in disguise. Write-failure rate itself might be a mediator rather than a confound (per the existing analysis, seed 20260913 had both the shortest same-node gap and by far the highest write-failure rate) — the experimental design should try to check whether spacing predicts healing outcome independent of write-failure rate, not just whether both correlate with the same outcome. Optimizer/segment-merge activity (per the sibling follow-on experiment on the same PR #6) is a separate, unrelated potential confound on `index_recall`-side metrics specifically, but this experiment's primary metric (missing-write recovery / completeness) is a data-content metric, not a graph-quality metric, so that particular confound is less directly relevant here — worth noting explicitly so the two follow-on experiments aren't conflated.


---

## Amendment 1: conditions are defined on REALIZED spacing, not requested

PR #18's live validation (`../qdrant_kill_scheduler/SPEC.md`) measured what the
scheduler actually delivers on a real cluster:

| condition | requested gap | realized gaps |
|---|---|---|
| short-gap-same-node | 5.0s | 1.53s, 2.17s |
| long-gap-same-node | 40.0s | 37.10s, 36.24s |

`DockerContainer.start()` returns only once Docker has actually restarted the
container, so a node comes back later than `kill + down_for_s` implies and the
gap absorbs the difference: **+3.08 to +3.58s**, near-constant on this host.

A near-constant ~3.3s removes 66% of a 5s gap and 8% of a 40s one. The
conditions therefore end up *further* apart in realized terms than requested,
and both still fall where the derived catch-up distribution (median 16.0s, p90
26.4s) says they should - the short condition far below the median, the long
one above the p90. **The design survives; the labels do not.** Reporting the
short condition as "5s" would misstate it threefold.

So: every figure this experiment reports about spacing is the realized value,
read from each run's own `realized_gap_s`. Requested values appear only as the
knob that was turned. This is Amendment 1 and it is the reason PR #18 was split
from this experiment in the first place - had the sweep run first, it would have
produced hours of data labelled with gaps wrong by 66% at the short end.

**Also inherited, and not to be overstated:** the latency figure is one host,
one Docker daemon, one image, three runs. Nothing here should cite ~3.3s as a
property of Qdrant. It does not need to be universal, because conditions are
defined on what each run recorded.

## Amendment 2: the write stream must still be flowing during chaos

The issue's Experimental design says "everything else stays identical to the
established protocol" without pinning the corpus size. Checked against PR #18's
three validation runs before committing any compute here, and it matters more
than it looks:

| | validation runs (20k vectors) | PR #6 sweep (100k vectors) |
|---|---|---|
| `corpus_exhausted` | **True** | False (mostly) |
| `write_failed` | **0** | 1,312 - 5,280 |
| worst missing ids | **0** | 1,573 - 9,779 |

All three validation runs produced **zero missing writes**, so the healing
metric this experiment is built on would have been identically zero in every
condition. The cause is not the kill schedule: with 20,000 vectors and 4
writers, the writer finished the entire corpus at t~35s, before the first kill
landed. Kills against a cluster that is no longer being written to cannot drop
a write, so there is nothing to heal from and nothing to compare.

The dependent variable only exists while writes are in flight. This experiment
therefore uses `--sift-vectors 200000` (the full cached SIFT base) so the
corpus cannot exhaust inside the run, and `corpus_exhausted` is checked per run
as a validity precondition: **any run that exhausts its corpus during or before
the chaos window is void and is re-run, not analyzed.** Recorded here so that
discarding such a run later cannot look like discarding an inconvenient result.

## Amendment 3: the pre-registered parameters

Held constant across all three conditions: corpus (SIFT, 200k), writers (4),
kill count (3), fixed down-time (4.5s, from the scheduler), chaos-window
duration, probe protocol, settling window, quiesce window, and the pinned
Qdrant image digest.

```
--dist sift --sift-vectors 200000 --writers 4 --queries 100 --k 10
--warmup-s 15 --pre-chaos-s 25 --chaos-duration 110 --duration 200
--kill-schedule <condition> --kill-count 3
```

Seeds: 20260940-20260944, the same five for each condition, so the comparison
is paired by seed rather than by independent draws.

Chaos window sizing: the long-gap condition needs 2 x (40 + 4.5) + 4.5 = 93.5s,
which fits 110s with margin. The window is identical for all conditions even
though short-gap needs only 23.5s of it, so that total chaos-window duration is
held constant rather than co-varying with the independent variable.

**Primary metric** (unchanged from the issue): absolute missing-write recovery
across the quiesce window - per `DECISION_LOG.md`'s dilution-trap entry, counts
and not the completeness ratio. **Secondary:** write-failure rate during chaos.

**Statistics:** exact two-sided Mann-Whitney per condition pair, 5v5, floor
p=0.0079 - the project's established test. With three pairwise comparisons on
the primary metric the floor is a real constraint, and per project convention
effect sizes and per-seed values are reported alongside, never instead of, the
p-value. n=5 per condition is the pre-registered N; it is not enlarged after
seeing results.

## Amendment 4: what would make this experiment uninterpretable

Written before the runs so it cannot be rationalized afterwards:

- **Any condition producing zero missing writes across all 5 seeds.** Then that
  condition has no dependent variable and the comparison involving it is void,
  not "healed perfectly."
- **Realized short gaps overlapping realized long gaps** across runs. The
  conditions must remain separated in realized terms; if a slow restart pushes a
  short gap into long-gap territory, that run's condition assignment is
  contaminated and it is reported as such.
- **`killed_while_down` firing.** The short-gap condition can kill a container
  that has not finished restarting, which is a different fault from the one
  under study. Every occurrence is reported; if it is common in short-gap runs,
  the condition is measuring two things at once and the result is qualified
  accordingly rather than presented cleanly.
