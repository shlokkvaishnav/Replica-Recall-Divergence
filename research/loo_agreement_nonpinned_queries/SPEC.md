# Spec: experiment/loo-agreement-nonpinned-queries

**Branch:** `experiment/loo-agreement-nonpinned-queries`
**Date opened:** 2026-08-23
**Status:** DRAFT — no implementation or results yet. This file is committed before either exists.

Issue: closes #5. Body copied verbatim below (per `research/AGENT_PIPELINE.md`'s implementer instructions — this is the issue text unmodified, not a paraphrase).

## Research question

Does `loo_agreement`'s ability to identify the degraded replica (measured in Layer 1/3 against a pinned, seeded query set) survive when the probe query workload is instead drawn from a non-pinned, realistic distribution (e.g. resampled per query round from the corpus's own query log / a held-out slice of SIFT1M's query set, or freshly sampled each round rather than fixed) — or does its above-chance detection accuracy collapse once queries stop being identical across replicas and across samples?

## Hypothesis

`loo_agreement`'s detection signal partially depends on the query set being pinned and identical across replicas: with a fixed query set, all healthy replicas are being asked to agree on the same hard/easy queries, which sharpens any real disagreement caused by one replica's data loss or graph degradation. Once queries vary per sample (still drawn from the same true distribution, but not identical draws), some of that agreement/disagreement will be swamped by ordinary per-query variance across differently-sampled query sets, so detection accuracy above chance should still hold but with reduced discriminative power (a smaller effect size / lower separation) versus the pinned-query result already established.

## Null / alternative hypothesis

Null: `loo_agreement` computed under a non-pinned query workload identifies the true lowest-`index_recall` replica at a rate statistically indistinguishable from chance (1/n replicas per shard, per the existing Q4 chance-baseline framework in `research/replica_recall/README.md`), i.e. the detector's above-chance performance was an artifact of query pinning rather than a property of the underlying agreement signal. Alternative (as hypothesized above): non-pinned `loo_agreement` still beats chance, but with a measurably smaller gap between detection accuracy (or the underlying agreement-score separation between the true degraded replica and its peers) than the pinned-query condition — a quantifiable robustness cost, not a binary pass/fail.

## Motivation

This is item #5 on the top-level README's "Open research questions / next experiments" list ("Detector robustness — does `loo_agreement` still work against non-pinned, realistic query traffic?"), and it targets a load-bearing methodological choice that is currently untested: `research/replica_recall/README.md` states plainly that "the query set is pinned and seeded. Identical at every sample and across runs, so recall differences are attributable to the cluster, not the queries" — this was a deliberate design decision to isolate the cluster's contribution to divergence, but it also means the current Layer 3 result ("can a ground-truth-free peer-agreement signal detect the degraded replica?") has only been shown to hold under a query workload no real production system would ever serve. If `loo_agreement` is going to be proposed as a production detector (the project's stated Layer 3 goal), it needs to survive the traffic shape it would actually see. This doesn't touch replication, the fault model, or the dataset — it isolates the query-distribution axis specifically, per `GIT_WORKFLOW.md`'s isolation rule.

## Experimental design

System: nano-db, same 2-shard × 3-replica topology as the existing Layer 1 experiment. Same node-kill chaos protocol (`chaos_harness.py`), same corpus (SIFT1M), same settling window and baseline-first protocol as `research/replica_recall/run_experiment.py`. The only change: replace the pinned, seeded query set with a non-pinned query workload for the `loo_agreement` probes specifically — concretely, hold out a slice of SIFT1M's own query set (distinct from any queries used to compute `index_recall`/`e2e_recall` ground truth) and draw a fresh random sub-sample of that held-out pool at each probe round, rather than reusing one fixed set. `index_recall`, `completeness`, and `e2e_recall` keep using their existing ground-truth-backed pinned protocol unchanged (they need a fixed query set to be comparable across samples) — only the queries fed into the `loo_agreement` computation change. Run the same 5-seed sweep (baseline + chaos) used for the existing headline result, so this experiment's numbers are directly comparable to the already-established pinned-query numbers.

## Metrics

Primary: `loo_agreement`'s detection accuracy — does the lowest-scoring replica by `loo_agreement` match the true lowest-`index_recall` replica, measured per sample, aggregated across the 5-seed sweep, against the existing 1/n chance baseline (same test used for the current Q4 result). Secondary: the magnitude of separation between the degraded replica's `loo_agreement` score and its healthy peers' scores (effect size, not just a win/loss count), to compare directly against the pinned-query condition's separation rather than only a pass/fail chance-beating claim.

## Baselines / controls

(1) The existing pinned-query `loo_agreement` result from the Layer 1/3 experiment, already established in this repo, as the direct comparison point — same topology, same chaos protocol, same corpus, only the query-selection protocol differs. (2) A no-chaos baseline run under the new non-pinned query protocol, to establish the noise floor for the new setup on its own terms (ordinary ANN nondeterminism plus query-sampling variance, absent any real degraded replica) before comparing it against the chaos condition. (3) The 1/n chance baseline for detection accuracy, as already used for Q4.

## Expected outcomes

(a) Non-pinned `loo_agreement` still beats chance with separation comparable to the pinned-query result — pinning wasn't load-bearing, and the detector is more production-ready than assumed. (b) Non-pinned `loo_agreement` beats chance but with measurably smaller separation than pinned — the hypothesized outcome — meaning the detector is usable but degraded by query variance, which would motivate a follow-up on how large a query sample per round is needed to recover the pinned-query separation. (c) Non-pinned `loo_agreement` performs at or near chance — the detector's above-chance result depended on query pinning as a confound, and the current Layer 3 "hypothesis under active investigation" claim in the top-level README would need to be walked back to "detection works only under a pinned, non-representative query workload," a materially different (weaker) claim than currently stated. (d) Results confounded by an insufficient held-out query pool size relative to SIFT1M's query set (too few distinct queries to sub-sample meaningfully across 5 seeds × multiple rounds), which would need to be ruled out before trusting (a)/(b)/(c).

## Interpretation plan

(a) would mean the current DO-NOT-CLAIM caveat about generalization to "production deployments" can be narrowed slightly — the query-pinning caveat specifically would no longer need to be carried, though everything else in DO NOT CLAIM (single system, single implementation) still stands. It would NOT mean the detector generalizes beyond nano-db or beyond SIFT1M-shaped query distributions. (b) would mean `loo_agreement` is a real but weaker production signal than the pinned-query numbers suggested, and would motivate a follow-up experiment on query-sample-size sensitivity (how many queries per round are needed to close the gap) rather than abandoning the detector. (c) would mean the Layer 3 hypothesis needs to be reframed in the README as "detection under a pinned query workload only" rather than left as an open general hypothesis, and would raise the priority of finding a workload-robust alternative before any production-detector claim is made. It would NOT mean peer-agreement detection is impossible in general — only that this specific pinned-query instantiation doesn't transfer. (d) would mean the experiment needs a larger or synthetically-extended held-out query pool before any of (a)/(b)/(c) can be trusted, and should be reported as inconclusive rather than folded into a directional claim.

## Confounds considered

Held-out query pool size: SIFT1M's own query set is finite (10,000 queries); sub-sampling from too small a held-out slice across many rounds/seeds could itself reintroduce near-pinning by exhausting the pool's diversity, which would bias toward outcome (a) for the wrong reason — needs to be checked explicitly (e.g. reporting how much overlap exists between rounds) rather than assumed away. Ground-truth leakage: the held-out query slice for `loo_agreement` must not overlap the queries used to compute `index_recall`/`e2e_recall` ground truth, or a change in one metric could spuriously correlate with the other through shared queries rather than through the phenomenon under test. Statistical floor: this inherits the existing n=5 seed-sweep floor (exact Mann-Whitney at 5v5 bottoms out at p=0.0079), so — per the existing project convention — effect sizes and per-run separation should be reported alongside, not instead of, any significance test. Distributional mismatch: if the held-out query slice happens to differ systematically in difficulty from the query set used for the existing pinned result (e.g. by chance drawing easier or harder queries), an observed change in separation could reflect query-set difficulty rather than the pinned-vs-non-pinned manipulation itself — mitigated by drawing the held-out pool from the same SIFT1M query distribution and, ideally, checking that per-query difficulty (e.g. mean k-NN distance) is comparable between the pinned set and the held-out pool.

---

## Results

*(Not yet — no implementation exists on this branch. This section stays empty until an experiment actually runs.)*

## Interpretation

*(Not yet.)*

## Decision

*(Not yet — DRAFT until results exist. See `GIT_WORKFLOW.md`'s merge criteria before deciding.)*
