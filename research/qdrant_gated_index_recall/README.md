# Does chaos degrade Qdrant's `index_recall` on a gated (indexed) corpus?

Issue #30 · branch `experiment/qdrant-gated-index-recall` (stacked on #29's `method/qdrant-index-gate`) · README open question #1, step 2.

**Answer.** Yes, at the replica level. With the corpus HNSW-indexed before measurement (#28's gate at `indexing_threshold` 1,000 KB) and each sample conditioned on its replica being ≥95% indexed, the worst replica's `index_recall` under node-kill chaos is below baseline in every one of five seeds — **0.978 vs 0.990, p = 0.0079**, the floor at 5 vs 5 — and the worst replica is the killed node in 4 of 5 runs. The cluster-wide six-replica mean does **not** separate (p = 0.31): the effect is one replica's, and averaging hides it. Full results, the amendment, and what is not established: [`SPEC.md`](SPEC.md).

**Why PR #6 saw nothing.** Its corpus was never indexed (`../qdrant_optimizer_masking/`), *and* its analysis averaged over replicas. Either alone would have hidden this.

## What was built

- `../cross_system_replication/qdrant_probe.py` — `search_batch(exact=False)`: `SearchParams.exact` on request, default off.
- `../cross_system_replication/qdrant_run_experiment.py` — `--score-at-gate`: with `--index-gate`, score every replica on the query set immediately before the gate, after it, and after it with `exact=True`, into `gate_scores.json`. Off by default.
- `sweep.py` — the pre-registered runs, pinned, over `qdrant_sweep.py` (the repo's sweep tool, not a hand loop). `run0` is the instrument check; `sweep` is 5 seeds × {baseline, chaos, quiesce} with PR #6's protocol.
- `analyze_gated.py` — joins `samples.csv` to `telemetry.csv` (replica == node, nearest `t_rel`); per-seed worst-replica `index_recall` conditioned on that replica's indexed fraction (pre-registered), unconditioned, and strict (every replica ≥ bar); exact Mann–Whitney U reused from `../replica_recall/aggregate.py`. Committed before any run.
- `results/` — 15 run directories, `run0/` (exact = 1.000 on all six, HNSW 0.996–0.999), `run0_before_after/` (the uninformative first spot-check, kept), `analysis_output.txt`, `aggregate_output.txt`.

## Reproducing

```bash
python research/qdrant_gated_index_recall/sweep.py run0      # ~5 min; must show exact=1.000, HNSW<1.000
python research/qdrant_gated_index_recall/sweep.py sweep     # ~65 min, 15 runs
python research/qdrant_gated_index_recall/analyze_gated.py   # the table in SPEC.md
python research/replica_recall/aggregate.py --sweep-dir research/qdrant_gated_index_recall/results   # the cluster-mean view
```

One host, one image digest, 100k SIFT vectors, k = 10. `index_recall` has ~1% of headroom at this scale; the separation reached the floor inside it.
