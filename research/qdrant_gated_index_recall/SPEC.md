# Spec: experiment/qdrant-gated-index-recall

**Branch:** `experiment/qdrant-gated-index-recall` (stacked on `method/qdrant-index-gate`, PR #29, until that merges)
**Issue:** #30 (body copied verbatim below, per `AGENT_PIPELINE.md`)
**Date opened:** 2026-09-04
**Status:** COMPLETE

### Type

experiment (one specific, narrowly-scoped experiment)

### Research question

Does node-kill chaos measurably degrade Qdrant's replica-level `index_recall` — the graph-quality metric — when the corpus is HNSW-indexed for the measurement window, and each sample records how indexed the corpus actually was when it was taken? This re-runs PR #6's 5-seed cross-system sweep with the indexing gate from #28 (PR #29), and is README open question #1, step 2.

### Hypothesis

`index_recall` separates between baseline and chaos on Qdrant once the measurement interrogates a graph, in the direction nano-db shows (`../replica_recall/RESULTS.md`: chaos damages the graph independently of what data is missing), but with smaller effect: Qdrant restarts a killed node into a segment set that is re-indexed within ~5–17s (#29's chaos run), so damage should be brief. Expected magnitude is unknown — PR #6 measured ~1.0 on both arms because it measured exact scans (`../qdrant_optimizer_masking/`), so there is no prior effect size on this system.

### Null / alternative hypothesis

No separation at the 5-vs-5 Mann–Whitney floor (p ≥ 0.0079 two-sided, exact) *after* conditioning each sample on indexed fraction ≥ 0.95 on the replica scored. Two alternatives that would look like a null but are not: (i) separation exists only in samples whose indexed fraction is below the bar — i.e. "damage" is the appendable tail being served by exact scan, not graph damage; the per-sample indexed fraction is recorded precisely to separate these; (ii) `index_recall` on this system sits at 1.0 ± noise even on a gated corpus because HNSW at `ef_construct 100 / m 16` over 100k SIFT vectors recovers the exact top-10 anyway, in which case the metric has no headroom and the question needs a harder query set, not more seeds.

### Motivation

The cross-system claim currently reads "graph-quality axis untested, not null" (`DECISION_LOG` 2026-09-02). #29 made the instrument able to say what it is measuring. This is the first experiment that can put a number, or an honest null, on that axis — and it is the step README priority #1 has named since the withdrawal. Either outcome changes the README: separation makes the nano-db finding a cross-system one; a clean conditioned null makes "replication damages data but not graphs on Qdrant" a claim with a measurement behind it rather than an artifact.

### Experimental design

Exactly PR #6's pre-registered protocol with the gate on, so the only variable that changed between #6 and this is *whether the corpus was indexed*:

- System/topology/fault model unchanged: Qdrant at `qdrant_topology.py`'s pinned digest, 2×3, `docker kill` randomized chaos, 4 writers.
- 5 seeds, **new**: 20261000–20261004 (not #6's, so this is a replication with the instrument fixed, not a re-analysis).
- Conditions: `baseline` and `chaos`, `--duration 120`, matched scale; `quiesce` (pre 20 / chaos 50 / quiesce 50) as in #6, since healing variance is still open.
- Corpus: `--warmup-until-written 100000` on a 250k pool (#28 Amendment 2), `--indexing-threshold-kb 1000`, `--index-gate --index-gate-tol 0.05`, `--capture-telemetry`. 100k rather than 200k: #29's 100k cells closed 2/2 at 1,000 KB and `probe_s` is 1.2–1.9s there vs 2.5–3.0s at 200k (PR #25's floor).
- Orchestration: extend `qdrant_sweep.py` with a `--conditions`/flag pass-through rather than a hand loop (`cross_system_replication/README.md`, "Running a sweep"); it already forwards unknown flags.
- Every `samples.csv` row gains, at analysis time, the indexed fraction of its replica at that `t_rel` from `telemetry.csv` (nearest telemetry sample; both run at `--sample-interval`).

**Instrument characterization (required by `SPEC_TEMPLATE.md`; the spot-check #29 owed):**

- *Run 0, before the sweep:* one gated `--no-chaos` run at these parameters, with `index_recall` scored on the same query set **immediately before** the gate closes (writers paused, tail un-indexed) and **immediately after** — the harness needs a small hook to score once at each point. Prediction: after-gate `index_recall` is *lower* than before-gate on the previously-un-indexed tail (exact scan returns true neighbours; HNSW returns approximate ones). If it is not lower, `indexed_vectors_count` does not mean searches traverse the graph and the sweep must not run until that is understood.
- Sampling interval realized: ~4–5s at 100k (#25, #29 `probe_s` 1.2–1.9s + score). Signal lag: damage appears a median 14.1s after a kill (#25). Ratio ≥3 samples per episode holds at 100k.
- What the metric measures: `index_recall` per replica vs brute-force ground truth over that replica's own local ids (`metrics.py`), so it is insensitive to completeness by construction; the per-sample indexed fraction is the state that must hold for it to mean graph quality.

### Metrics

Primary, decides the outcome: per-seed mean `index_recall` on the worst replica, chaos window vs baseline, **restricted to samples with replica indexed fraction ≥ 0.95**; exact two-sided Mann–Whitney U at n = 5 vs 5 (floor p = 0.0079, `aggregate.py` unchanged). Secondary: the same over all samples (what #6 would have seen), the unconditioned-minus-conditioned difference (how much "damage" is tail exact-scan), `completeness`/`e2e_recall` for continuity with #6, and the fraction of chaos-window samples that met the bar (from #29: expect ~0.9).

### Baselines / controls

`baseline` (gated, no chaos) is the noise floor for `index_recall` on an indexed corpus — needed because #6's baseline was an exact-scan floor of 1.0 and says nothing about HNSW noise. Run 0 is the control for the metric's meaning. #6's own 5-seed result (`results_sweep/`) is the un-gated comparator: same protocol, un-indexed corpus.

### Expected outcomes

(a) Conditioned `index_recall` separates (p ≤ 0.0079) and unconditioned does too: graph damage is real on Qdrant. (b) Conditioned does not separate but unconditioned does: the "damage" is the un-indexed tail being exact-scanned in one arm more than the other — a measurement artifact #29 predicted and this design can name. (c) Neither separates and baseline `index_recall` is ≈1.0 with no spread: the metric has no headroom at this scale/query set — outcome (ii) above. (d) Neither separates and baseline `index_recall` shows spread (<1.0, seed-varying): a genuine null on a working instrument. (e) Run 0's prediction fails: stop; the instrument does not mean what it claims.

### Interpretation plan

(a) → README priority #1 gains a cross-system positive on the graph axis; DECISION_LOG entry; the Weaviate step becomes the next experiment. (b) → README's "untested, not null" becomes "no graph damage detectable once tail exact-scan is removed" with the effect size of the artifact recorded — and PR #6's withdrawn finding gets a precise epitaph. (c) → a `method/*` issue for a harder query workload (README open question #5 territory), not more seeds. (d) → a conditioned null at the pre-registered floor; record as such, do not re-run. (e) → `method/*` issue on what `indexed_vectors_count` measures; this experiment waits.

### Confounds considered

Lowering `indexing_threshold` to 1,000 KB changes segment layout and therefore graph construction relative to #6 — held constant across both arms here, and named as a deviation from Qdrant defaults in the writeup. The tail regrows during the window (#29: 0.87–0.98); conditioning on ≥0.95 per sample handles it but reduces n per seed — report the retained-sample count per arm, and if either arm retains <50% of samples the conditioned comparison is under-powered and must say so. Restart re-indexing (5–17s) overlaps the damage lag (14.1s median): a kill's graph damage and its re-index may cancel within one sample — the telemetry lets this be seen per event rather than guessed. Randomized kill timing (as #6) not the #17 scheduler, deliberately, to stay matched to #6. One host; timings are this machine's.

### Before submitting

- [x] I checked README.md's "Open research questions" and research/DECISION_LOG.md and this isn't a duplicate or already-ruled-out question.
- [x] This is one answerable question, not a broad restatement of the whole research thesis.


---

## Amendment 1 (2026-09-04, after run 0, before any sweep run): the before/after-gate spot-check cannot create its own contrast; replaced by HNSW-vs-exact on the same state

Run 0 (`results/run0_before_after/`, seed 20260999, 100,320 confirmed writes at gate) scored the six replicas immediately before and after the gate and got **identical `index_recall` on every replica** (0.996–1.000, deltas all 0.0). Not because the instrument failed: the gate closed in **2.1s** — the corpus was already 97.8% indexed at the first poll (98,161–99,206 of 100,416 per node), because at 1,000 KB the optimizer keeps pace during the write phase. "Before" and "after" were the same state. The pre-registered prediction was untestable as designed, which this spec's outcome (e) treats as a stop — and this amendment is that stop, before any sweep cell runs.

**What run 0 does establish, on the way:** baseline `index_recall` over 42 samples is 0.991–1.000 (median 0.997). Scored against brute-force top-k over the replica's own local ids, an exact search returns 1.000 by construction; values below 1.000 can only come from an approximate path. That is already evidence the graph is traversed — but it is an inference from a distribution, not a controlled contrast.

**Replacement check, pre-registered here:** at the after-gate moment, score every replica twice on the same query set — default (HNSW) and with `SearchParams.exact = true` (`qdrant_probe.search_batch(exact=True)`, new, default off). Predictions: exact = 1.000 on every replica; HNSW < 1.000 on at least one. If exact ≠ 1.000 the scorer's ground truth is wrong and *that* is the finding; if HNSW = 1.000 everywhere, the metric has no headroom at this scale (outcome (c)) and the sweep still should not run. Only exact = 1.000 ∧ HNSW < 1.000 releases the sweep.

**Also noted for the Interpretation:** 0.991–1.000 leaves ~1% of headroom for chaos to reduce. Outcome (c) is the likeliest; the Interpretation plan for (c) stands (a harder query workload is a `method/*` issue, not more seeds).

No metric, baseline, or outcome definition changes; run 0 is re-run as `results/run0/` with the new flag behaviour, and its first output is kept as `results/run0_before_after/`.

---

## Results

Fifteen runs, `results/seed<N>_<condition>/` (qdrant_sweep.py's layout), seeds 20261000–20261004, plus `results/run0/` (Amendment 1) and `results/run0_before_after/` (the uninformative first attempt, kept). Every gate closed (2–22s) at 100,000–100,672 confirmed writes on a 1,000 KB threshold. `analyze_gated.py` regenerates every number below into `results/analysis_output.txt`; `../replica_recall/aggregate.py`, unmodified, into `results/aggregate_output.txt`.

**Run 0 (instrument):** on one post-gate state, `SearchParams.exact` scored **1.000 on all six replicas**; the default HNSW path scored **0.996–0.999 on all six**. The graph is traversed; `indexed_vectors_count` means what the gate assumes.

**Primary, pre-registered — worst replica per round, conditioned on that replica ≥0.95 indexed:**

| seed | baseline | chaos | retained (base / chaos) |
|---|---|---|---|
| 20261000 | 0.9906 | 0.9875 | 0.92 / 1.00 |
| 20261001 | 0.9918 | **0.9624** | 0.77 / 1.00 |
| 20261002 | 0.9913 | 0.9810 | 1.00 / 1.00 |
| 20261003 | 0.9900 | 0.9783 | 0.55 / 1.00 |
| 20261004 | 0.9875 | 0.9825 | 0.73 / 1.00 |

Baseline mean 0.9903 vs chaos 0.9783; every chaos seed below every baseline seed; exact two-sided Mann–Whitney **U = 25, p = 0.0079** — the floor at 5 vs 5. Unconditioned (what PR #6 would have computed with an indexed corpus): 0.9896 vs 0.9773, p = 0.0159. Strict (round kept only if every replica ≥ bar): 0.9896 vs 0.9779, p = 0.0556 — fewer rounds survive (1–12 per baseline run), not a different direction.

**Retained fraction** is 1.00 in every chaos run and 0.55–1.00 in baseline: chaos windows are *more* indexed than baselines, because each restart reloads a node whose appendable tail then re-indexes within seconds (#29), whereas a baseline's tail regrows untouched. The conditioned drop is therefore not the exact-scan tail; if anything the tail works against it.

**Where the drop is.** In 4 of 5 chaos runs the worst replica in *every* chaos-window round is a node that had been killed (100%; 0.77 in seed 20261002); in baseline runs the worst node rotates (e.g. `020100010111`). Per-replica chaos-window means show one replica carrying the loss while its peers do not: seed 20261001 `shard-0-2` 0.939 vs 0.986–0.997; seed 20261004 `shard-0-2` 0.973 vs 0.987–0.993; seed 20261002 `shard-0-0` 0.982 vs 0.990–0.996. Lowest single round: 0.845 (seed 20261001).

**Cluster-wide mean, for contrast:** `aggregate.py` averages `index_recall` over all six replicas and all samples and reports baseline 0.9924 ± 0.0013 vs chaos 0.9904 ± 0.0028, **p = 0.31 — no separation.** `completeness` (1.000 vs 0.993) and `e2e_recall` (0.996 vs 0.987) separate at p = 0.0079, as in PR #6.

**Quiesce (secondary):** post-chaos worst-replica mean `index_recall` 0.949, 0.991, 0.974, 0.986, 0.989 (n = 4–5 samples each) against a baseline range of 0.9875–0.9918: two of five stay below the baseline range after kills stop, one (seed 20261000, all four kills on node 2) at 0.949 with a minimum of 0.936.

## Interpretation

**Outcome (a).** Node-kill chaos measurably degrades Qdrant's replica-level `index_recall` when the corpus is HNSW-indexed and the measurement conditions on the replica being indexed: complete separation at the pre-registered floor, on the pre-registered metric, with the loss on the killed replica.

**Why PR #6 saw nothing, in two parts.** First, #6's corpus was not indexed (`../qdrant_optimizer_masking/`), so it measured exact scans — the correction already on record. Second, and new: even on an indexed corpus, the *cluster-wide mean* does not separate (p = 0.31 here). The effect is on one replica out of six, and averaging over six replicas dilutes a 1–5 point drop on one into a 0.2 point drop on the mean. This is the same shape as `DECISION_LOG`'s reason for scoring `loo_agreement` per replica rather than in aggregate, and it means the nano-db comparison has to be read at the same unit: `../replica_recall/RESULTS.md`'s `index_recall` separation is also a per-replica statement.

**Size, honestly.** The effect is ~1.2 points on the seed mean (0.990 → 0.978) and up to 5 points on the worst replica in one seed. `index_recall` at k = 10 over 100k SIFT vectors has ~1% of headroom below 1.0 — outcome (c)'s worry — so the separation is at the limit of what the metric can resolve, and it reached the floor anyway. A harder query workload (README open question #5) would enlarge the scale, not change the sign; that stays a `method/*` question.

**Mechanism, not established.** What a kill does to a persisted HNSW is not observed here. The candidates — WAL-replayed points landing in a fresh appendable segment whose links differ from the pre-kill graph; a reloaded index whose entry points differ; or plain missing points (the completeness loss) making the local ground truth easier and the graph relatively worse — are distinguishable only with per-segment instrumentation this harness does not have. The quiesce runs say the loss does not always heal within 50s, which is the shape of the nano-db result, but at n = 5 with 4–5 post-chaos samples each that is a hypothesis for a pre-registered healing experiment, not a finding.

**What this does not establish.** Anything about Weaviate or systems with real anti-entropy. That the effect scales with kill count or spacing (kill timing was randomized to stay matched to #6; #9/#24's line is closed). That the strict metric would reach the floor with more baseline rounds (p = 0.056 with 1–12 retained rounds). Single host, single image digest, 100k corpus, k = 10.

## Decision

**MERGE.** All nine criteria: relevance (README priority #1, step 2, the question named since the withdrawal); correctness (run 0's exact = 1.000 / HNSW < 1.000 contrast; every number recomputable by two independent tools, one of them unmodified from the nano-db experiment); validity (pre-registered metric, new seeds, matched protocol, conditioning on the instrument state); reproducibility (`sweep.py run0`, `sweep.py sweep`, `analyze_gated.py`); documentation (this spec, one amendment dated before the sweep); interpretation (the aggregate-mean null is reported next to the per-replica separation, not instead of it); integrity (run 0's first attempt kept as uninformative; the strict metric's p = 0.056 reported); integration (`search_batch(exact=)` default off, one new directory); evidence (15 runs at the pre-registered N).

**Changes the top-level README:** open question #1's graph-quality axis moves from "untested, not null" to **established on Qdrant at the replica level** — with the unit stated, because at the cluster level it is a null. The cross-system claim becomes: replica-level `index_recall` divergence under node-kill chaos is not specific to nano-db; the cluster-wide mean hides it on both systems' scale of effect. `DECISION_LOG` entry required.

**Follow-ons, each its own issue:** (1) `experiment/*` — does the per-replica `index_recall` loss heal on Qdrant, with a quiesce window long enough to say (the 50s here is a hint); (2) `method/*` — a harder query workload to enlarge the metric's headroom (open question #5); (3) Weaviate, per the Motivation.
