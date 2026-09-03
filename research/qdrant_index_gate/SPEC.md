# Spec: method/qdrant-index-gate

**Branch:** `method/qdrant-index-gate`
**Issue:** #28 (body copied verbatim below, per `AGENT_PIPELINE.md`)
**Date opened:** 2026-09-03
**Status:** COMPLETE

### Type

method (a new methodological component — metric, detector, protocol)

### Research question

Can the Qdrant harness guarantee that the corpus is HNSW-indexed on every replica *before* the timed baseline window opens — and hold it indexed through the measurement window at the write rate the protocol needs — so that `index_recall` interrogates a graph for the whole run rather than an exact scan? At what setup-time and parameter cost?

This is README open question #1's first named step ("re-measure Qdrant's `index_recall` with indexing front-loaded"), scoped to the *instrument*. The re-measurement itself is the follow-on `experiment/*` and is deliberately not this issue.

### Hypothesis

An indexing gate — write the corpus, then block until every node's `/collections/{name}` reports `indexed_vectors_count == points_count` (plateau on all replicas) before starting the baseline clock — is reachable at Qdrant's default `indexing_threshold` (20,000 KB, ≈40k 128-d vectors per segment) within a few minutes of setup at 100k–200k vectors, and stays ≥95% indexed through the chaos window once writes resume at the protocol's rate. Lowering `optimizers_config.indexing_threshold` at collection creation will shorten the gate but is expected not to be *necessary* for the gate to close.

### Null / alternative hypothesis

The gate does not close, or closes and then reopens. Concretely: (i) `indexed_vectors_count` on at least one replica never reaches `points_count` within a 10-minute cap at the default threshold — indexing is throttled by something other than segment size; or (ii) the gate closes, but once protocol writes resume, the un-indexed tail grows faster than the optimizer drains it, so fewer than 95% of samples in the chaos window see every replica ≥95% indexed — meaning "front-load indexing" cannot be reconciled with "writes in flight during chaos" (which `completeness` needs) without a lower threshold or a slower write rate; or (iii) restart-after-kill drops a replica back to un-indexed for longer than the kill itself, so the gate is a pre-chaos property only.

### Motivation

The cross-system comparison's graph-quality axis is currently **untested, not null** (`research/qdrant_optimizer_masking/`, `DECISION_LOG` 2026-09-02): PR #6's `index_recall` null was measured over a corpus that was un-indexed for the whole baseline and 60–84% of the chaos window, so it measured exact scans. Nothing about Qdrant's replicated HNSW under chaos can be claimed until the instrument measures a graph. This issue makes that claimable-or-refutable. It is also the first spec filed under `SPEC_TEMPLATE.md`'s new **Instrument characterization** requirement, and it exists *because* three prior sweeps failed on apparatus properties that were measurable in advance.

If (i)–(iii) hold, the finding is that Qdrant's indexing and this protocol's write model are in tension at this scale, which is itself worth recording — and the design must separate a write phase from a measurement phase, changing the protocol rather than the harness.

### Experimental design

System: Qdrant, image digest as pinned in `qdrant_topology.py`; 2×3 topology unchanged; SIFT1M subset, 128-d; `docker kill` fault model unchanged. **No chaos runs are needed to answer this issue's question** — it is about the write→index→measure pipeline, so most runs are `--no-chaos`; one chaos run per configuration checks (iii).

Harness changes on `method/qdrant-index-gate` (all additive, off by default):
1. `--index-gate`: after the pre-baseline write phase, poll every node's `/collections/{name}` (`indexed_vectors_count`, `points_count`, `status`) at 1s until all replicas report `indexed_vectors_count >= (1 - tol) * points_count` for N consecutive polls (tol and N are flags; defaults 0.0 and 3), or a `--index-gate-timeout` elapses, in which case the run **fails loudly** and writes no `samples.csv` (so `qdrant_sweep.py` records `FAILED`).
2. `--indexing-threshold-kb`: passed through to `optimizers_config.indexing_threshold` at collection creation; default unset (Qdrant default) so existing behaviour is unchanged.
3. `run_meta.json` records gate-closed time, per-replica indexed fraction at gate close, and the threshold used. `--capture-telemetry` (already merged) supplies the per-sample indexed fraction.

Runs: at each of {default threshold, 5,000 KB, 1,000 KB} × {100k, 200k vectors}: 2 `--no-chaos` runs at the protocol's write rate and duration, plus 1 chaos run at the default threshold and 200k to test (iii). 15 runs; each is short because there is no comparison to power.

Held constant: write rate, batch size, seed set, duration, topology, image. Varies: threshold, corpus size.

### Metrics

Primary, decides the outcome: **gate-close time** (s, from end of write phase), and **indexed-fraction-in-window** — per sample, min over replicas of `indexed_vectors_count / points_count`; report the fraction of baseline-window samples at 1.0 and of chaos-window samples ≥0.95.

Secondary: setup cost (wall-clock added to a run), `probe_s` before/after (does a fully-indexed corpus change probe cost — it changes what a search does), and for the chaos run, time-to-reindex after each restart from `telemetry.csv`.

Instrument characterization (from existing artifacts, `qdrant_optimizer_masking/results/`): first indexed vector appeared at t≈83s at 100k vectors/PR #6's write rate; 60% (seed 20260920) and 84% (seed 20260921) of chaos-window samples were un-indexed; indexing never completed within a 150s run. Sampling interval for telemetry matches the probe sampler (~4s realized, PR #25). So the quantity under study moves on a scale of tens of seconds and is sampled every ~4s — resolution is adequate for this question, unlike #25's.

### Baselines / controls

The control is the current harness with no gate and default threshold, run under the same telemetry — i.e. PR #11's runs, plus one fresh run so the number is from this branch's image and machine. Without it, "the gate closed in 140s" has no reference for how un-indexed the corpus would otherwise have been at that moment.

### Expected outcomes

(a) Gate closes at the default threshold within ≤5 min at 200k, and ≥95% of chaos-window samples stay ≥0.95 indexed: front-loading alone is enough; threshold stays default.
(b) Gate closes only with a lowered threshold, or closes at default but the chaos window drops below 0.95 as writes outpace the optimizer: the follow-on experiment must pin a non-default threshold and record it as a protocol parameter.
(c) Gate does not close within the cap at any threshold, or closes but restarts leave a replica un-indexed for most of the chaos window: continuous writes and graph measurement cannot coexist at this scale; the protocol needs a write phase separated from a measurement phase, which is a *different* protocol than nano-db's and must be argued for rather than slipped in.
(d) Gate closes but `probe_s` rises enough that the realized sampling interval breaches #25's floor again — the instrument fix trades one apparatus limit for another.

### Interpretation plan

(a) → file the follow-on `experiment/*` re-running PR #6's 5-seed sweep with `--index-gate`; the graph-quality axis becomes testable. Does **not** mean Qdrant's graph diverges or doesn't — nothing here measures divergence. (b) → same follow-on, with threshold recorded as a deliberate deviation from Qdrant defaults and a sentence on why that does not bias the comparison (it changes when segments index, not how search behaves once indexed). (c) → record in `DECISION_LOG`; the follow-on becomes a two-phase protocol proposal, and the cross-system claim must be re-scoped to what is comparable across systems with different write/index coupling. (d) → couple with the digest-based completeness probe PR #25 proposed before the follow-on; do not run the sweep against a known-breached floor.

### Confounds considered

`indexed_vectors_count` is Qdrant's own report, not ground truth — a segment reported indexed could still be searched by exact scan if the optimizer's status is mid-rebuild; cross-check `status` and `segments_count`, and spot-check one node by comparing `index_recall` before and after the gate on the same query set (indexed search should *lower* it from exactly 1.0). Lowering the threshold changes segment layout and therefore graph construction, which could itself alter chaos sensitivity — the follow-on must hold one threshold across all its conditions. Machine dependence: indexing speed is CPU-bound and this host's number; record it as such, as #19 did for restart latency. Two runs per cell is a pilot for timing, not a distribution — report ranges, not means.

### Before submitting

- [x] I checked README.md's "Open research questions" and research/DECISION_LOG.md and this isn't a duplicate or already-ruled-out question.
- [x] This is one answerable question, not a broad restatement of the whole research thesis.


---

## Amendment 1 (2026-09-03, before the sweep): `tol = 0` is unreachable by construction; the sweep gates at `tol = 0.05`

Found by the two harness smoke runs, not by the sweep (50k vectors, 30s warmup, `--no-chaos`, gate timeout 300s):

- **Default threshold (20,000 KB):** every node reported `0 / 48,512` indexed for the full 300s, 4 segments each, `status green`, `optimizer_status ok`. 48.5k × 512 B ≈ 24.8 MB across 2 shards ≈ 12.4 MB per shard — no segment ever crosses the threshold, so nothing indexes. The gate failed as designed (exit 3, `index_gate_failed.json`, no `samples.csv`). Not a finding about the protocol — a 50k corpus is below Qdrant's default indexing floor — but it explains PR #11 exactly: at 100k (≈25 MB/shard) the first segment barely clears 20 MB, which is why indexing first appeared at t≈83s and never completed within the run.
- **1,000 KB threshold:** indexing kept pace with the writers during warmup. At the moment the gate opened every node was already at its plateau — `32,419 / 35,040` (0.925), `32,401` (0.925), `33,192` (0.947) — and stayed there, unchanged to the vector, for 300s. Qdrant keeps one *appendable* segment per shard un-indexed to receive writes, and reports its points in `points_count` but never in `indexed_vectors_count`. The residual is therefore structural, not lag: `indexed_vectors_count == points_count` cannot occur while a collection accepts writes at all.

**Consequences for this spec:**

1. The Hypothesis section's gate condition, "`indexed_vectors_count == points_count`," is amended to "`indexed_vectors_count >= (1 − tol) · points_count` with `tol = 0.05`." The harness flag existed for this reason; its default stays 0.0 so a run that asks for the impossible fails loudly rather than silently passing at some hidden tolerance.
2. The residual fraction at gate close becomes a **reported quantity** (`min_fraction_at_end` in `run_meta.json`): it is the size of the appendable tail relative to the corpus and is expected to fall with corpus size at a fixed threshold (≈2.6k vectors ≈ 7.5% at 35k; ≈1.3% at 200k) and to be *larger than `tol`* at the default threshold, where the tail can be up to one full sub-threshold segment. A default-threshold gate that fails on the residual alone — every replica plateaued, `optimizer_status ok`, fraction stable across `consecutive` polls — is outcome (b), not (c), and `analyze_gate.py` must distinguish "never indexed" from "plateaued below tol."
3. 0.05 is chosen so that the 200k cells can close at 1,000 KB and 5,000 KB with margin, and so that the default-threshold cells *cannot* close if the tail is one 20 MB segment (≈20% at 200k) — the sweep then measures the tail rather than assuming it. It is not tuned to any observed number beyond the two runs above.

Nothing in the Metrics, Baselines, or Expected outcomes sections changes; (b) simply gains the plateau mechanism as its likeliest cause.

## Amendment 2 (2026-09-03, after one sweep cell, before any result is used): the gated corpus must be a controlled size

The first sweep cell, `thrdefault_n100k_nochaos_seed20260970`, reported `never-indexed` after a 600s gate. Its `index_gate_failed.json` shows why, and it is not a Qdrant result: every node held **66,816** points, not 100,000. `--sift-vectors` sizes the *pool* the writers draw from; the corpus at gate time was write rate × `--warmup-s` = ~1.6k/s × 40s. At 66.8k points ≈ 17 MB per shard, below the 20 MB default threshold, "never indexed" is the expected outcome for a corpus this spec did not intend to test. The cell is moved to `discarded_uncontrolled_corpus/` (kept, not deleted, per `GIT_WORKFLOW.md`) and excluded from analysis; the sweep was stopped after it rather than producing twelve more mislabelled cells.

**Changes:**

1. Harness: `--warmup-until-written N` extends the warmup until N writes are *confirmed*, failing the run (exit 4) if the pool exhausts or `--warmup-cap-s` elapses first. `run_meta.json` records `warmup_until_written` and `written_at_gate`. Without the flag the harness behaves exactly as before.
2. Sweep: each cell passes `--warmup-until-written n` with a pool of `n + 150,000`, so the writers can resume after the gate — the chaos cell needs writes in flight — and the driver's `run_meta` check now verifies `written_at_gate >= n`.
3. Instrument characterization, corrected: the quantity "corpus size" is now `written_at_gate`, a measured number, not a label. The 100k cells should land at ≈25 MB/shard — the boundary PR #11 was sitting on — and the 200k cells at ≈50 MB/shard.

Cost: the extended warmup at ~1.6k/s adds ≈25s (100k) to ≈85s (200k) per cell. No metric, baseline, or outcome definition changes.

## Amendment 3 (2026-09-04, after 12 of 13 cells, before the chaos cell's result): the outcome-(iii) chaos cell must run at a threshold whose gate closes

The pre-registered design put the single chaos run at the default threshold. The sweep's first twelve cells settled the gate question before that cell ran: **default 0/4 closed** (plateau 0.855–0.916), **5,000 KB 1/4** (0.846–0.930; the one close at 0.966 fell to 0.80 within the baseline), **1,000 KB 4/4** (close in 4–22s; baseline window 0.87–0.98, ≥0.95 in 8–62% of samples). A default-threshold chaos cell therefore times out at its gate and yields no restart data — outcome (iii) would be *unmeasured*, not answered. The cell is still run and kept, because it was pre-registered and its `GATE-FAILED` record is the evidence for this amendment.

**Change:** one additional chaos cell at 1,000 KB × 200k, seed 20260983, same `--chaos-duration 60 --pre-chaos-s 30`. It is the only cell that can answer (iii) — whether a restart drops a replica back below the bar for longer than the kill itself — and its `reindex_s` is computed by the analysis already committed. Nothing else changes; the 14-cell sweep replaces the 13-cell one in the Results.

**Also recorded here, not as a change:** the mechanism behind the plateaus, read from the live collection during cell 5 (`results/OBSERVATION_during_sweep.md`): `default_segment_number: 0` gives two segments per shard, one of which is the appendable segment; its contents are un-indexed until it alone exceeds `indexing_threshold`, and — consistent with the documented merge optimizer, though not observed directly — it is not merged while the segment count is at target. Amendment 1 called the tail "fixed-size"; it is *write-phase-sized*, which is why it did not shrink at 200k.

---

## Results

Fourteen cells (`results/`, one directory each; `analyze_gate.py` regenerates every number below into `results/analysis_output.txt`). Corpus at gate = `written_at_gate`, 100,1xx or 200,1xx in every cell. Gate: `tol 0.05`, 3 consecutive 1s polls, 600s cap.

| threshold | cells | gates closed | close time | plateau if failed | baseline ≥0.95 (per run) | min fraction in run |
|---|---|---|---|---|---|---|
| default (20,000 KB) | 5 (incl. chaos) | **0/5** | — | 0.855, 0.859, 0.875, 0.878, 0.916 | — | — |
| 5,000 KB | 4 | **1/4** | 13.6s | 0.846, 0.852, 0.930 | 0.04 (the one that closed: 0.966 → 0.80 by end of window) | 0.80 |
| 1,000 KB | 5 (incl. chaos) | **5/5** | 4.3–22.1s | — | 100k: 0.42, 0.08 · 200k: 0.62, 0.38 · chaos cell: 1.00 (6/6) | 0.873–0.936 |

Chaos cell, 1,000 KB × 200k (seed 20260983): 4 kills from the randomized chaos loop at t = 279, 298, 309, 322s (node 2, node 0, node 0, node 2; down for 8.0, 2.7, 3.5, 5.4s); chaos-window samples ≥0.95: **10/11**; time from restart back to ≥0.95: **6.7, 16.9, 4.7, 6.5s**.

`probe_s` median: 1.2–1.9s at 100k, 2.5–3.0s at 200k (1,000 KB), 3.6s at 200k (5,000 KB). Setup cost: extended warmup ≈25s (100k) / ≈85s (200k) plus the gate.

The pre-registered default-threshold chaos cell timed out at its gate (plateau 0.859), as Amendment 3 predicted; kept as `thrdefault_n200k_chaos_seed20260982`.

## Interpretation

**Outcome (b), sharpened.** A front-loaded corpus reaches the bar only when `indexing_threshold` is lowered to 1,000 KB. At the default and at 5,000 KB, every replica plateaus at 0.85–0.93 and stays there for as long as the gate waits: the appendable segment (one per shard, `default_segment_number` auto = 2/shard) holds everything written since the last merge, and is indexed only when *it alone* crosses the threshold. Why it is not merged into the indexed segment was not observed — Qdrant exposes no per-segment view here — but is consistent with the documented merge optimizer, which acts only when the segment count exceeds `default_segment_number`. That tail is write-phase-sized, not fixed — which is why it did not shrink at 200k — and why 5,000 KB is not meaningfully different from 20,000 KB at these sizes (`results/OBSERVATION_during_sweep.md`).

**Null hypothesis (ii) holds in a weaker form than feared.** Once writers resume, the tail regrows: at 5,000 KB the one closed gate fell from 0.966 to 0.80 within 120s; at 1,000 KB the optimizer keeps up well enough that the window oscillates 0.87–0.98 around ~0.93, with ≥0.95 satisfied in 8–62% of samples. So "indexed" during measurement means *mostly* indexed — a 5–13% exact-scanned tail is the steady state at this write rate, not a transient.

**Null hypothesis (iii) was not observed in the one 1,000 KB chaos run** (one seed, four kills): replicas were back above the bar 4.7–16.9s after restart, the same order as the 3–8s down-times plus Qdrant's ~3.3s restart latency (#19), and the chaos window stayed ≥0.95 in 10/11 samples. One run cannot refute (iii); it says the gate is not *obviously* a pre-chaos-only property, and the follow-on's five seeds will say more.

**Outcome (d) is partly present.** `probe_s` at 200k is 2.5–3.6s, the same `ListLocalIds`-dominated floor PR #25 found; an indexed corpus does not change it. Any follow-on at 200k inherits #25's sampling limit.

**What this does not establish.** Anything about `index_recall` under chaos — no comparison was run, and the cells' `index_recall` values were not analysed beyond noting the chaos cell's median (0.99). Whether a 93%-indexed corpus makes `index_recall` "measure a graph" is not answered by `indexed_vectors_count` alone — it is Qdrant's own count of vectors in indexed segments, not an observation of which search path served a query. The spot-check the Confounds section asked for (index_recall before vs after the gate on one node) was not done and is listed under Decision; it is the observation that would settle this. (An earlier draft claimed `hnsw_config.full_scan_threshold` forces exact scan of the tail segment; review round 1 found that parameter governs payload-filtered search only, and the claim is withdrawn.) Single host, single image digest, 2 repeats per cell: timings are ranges, not distributions.

## Decision

**MERGE the method; the instrument is validated infrastructure by the `graph_forensics.py` standard.** `--index-gate` refused eight un-indexed corpora loudly (exit 3, no `samples.csv`, `index_gate_failed.json`), closed six, and every closed gate is verifiable from `run_meta.json`; `--warmup-until-written` turned "corpus size" from a label into a measured quantity after the first cell showed the label was wrong; `--indexing-threshold-kb` reaches Qdrant and the effect is large and monotone. Three amendments were needed and each is dated before the cells it governs.

**Follow-on, as its own pre-registered `experiment/*` (README priority #1, step 2):** re-run PR #6's 5-seed sweep with `--index-gate --index-gate-tol 0.05 --indexing-threshold-kb 1000 --warmup-until-written 200000 --capture-telemetry`, reporting `index_recall` *alongside* per-sample indexed fraction so every sample states what it was measuring. Before that, one small method item: the before/after-gate `index_recall` spot-check this spec owed — the only observation here that would show searches traverse the graph rather than count the vectors that could be. (A second item in the earlier draft, exposing `full_scan_threshold`, was withdrawn in review round 1: that parameter governs payload-filtered search, which this harness does not use.)

**Not proposed:** a lower tolerance, a longer gate, or a bigger corpus. The plateau mechanism makes all three return the same answer.
