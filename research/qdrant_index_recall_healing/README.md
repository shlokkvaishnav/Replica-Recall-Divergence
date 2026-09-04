# Does the killed replica's `index_recall` on Qdrant heal after chaos stops?

Issue #35 · branch `experiment/qdrant-index-recall-healing` · README open question #1, healing.

**Answer.** Yes — it is a transient of the restart. With PR #31's gated protocol and a 180s quiesce (≈36 post-chaos samples instead of #31's 4–5), the worst replica's `index_recall` is back inside its own baseline range over the last 60s in **4 of 4 judged seeds**. After the last kill, one seed dropped to 0.946 for one 30s bin, one dipped 0.003 for one bin, and two showed nothing beyond noise — every dip was gone by the next bin. The fifth seed is **unmeasured**: its chaos window ran 127s on a 50s setting with zero kill events — a harness defect, kept and reported, not re-run. Completeness recovered 100% of missing ids in all four seeds (PR #6's 50s window had seen 0–100%). Full table, the noise-floor reading on the closest seed, and what is not established: [`SPEC.md`](SPEC.md).

## What was built

- `sweep.py` — `qdrant_sweep.py --only baseline` (240s) and `--only quiesce` (pre 20 / chaos 50 / quiesce 180 → `--duration 250`), PR #31's gate flags, seeds 20261100–20261104.
- `analyze_healing.py` — per seed: the baseline range of worst-replica conditioned `index_recall`; after the **last kill**, 30s-bin trajectory, last-60s mean vs the seed's own baseline minimum (primary), time-to-baseline, retention of conditioned rounds, the unconditioned view beside it, whether bin 0 shows a loss at all, the killed node's indexed fraction per bin, and worst completeness in the last 60s. A quiesce run with zero kills is reported as unmeasured. Reuses `../qdrant_gated_index_recall/analyze_gated.py`'s telemetry join. Committed before any run; the zero-kill rule was added after seed 20261100 showed the case, before any number was used.
- `results/` — 10 run directories, `analysis_output.txt`, `aggregate_output.txt`.

## Reproducing

```bash
python research/qdrant_index_recall_healing/sweep.py all        # ~70 min, 10 runs
python research/qdrant_index_recall_healing/analyze_healing.py  # the table in SPEC.md
python research/replica_recall/aggregate.py --sweep-dir research/qdrant_index_recall_healing/results   # completeness healing
```

One host, one image digest, 100k SIFT, k = 10. At 240–250s durations the appendable tail keeps indexed fractions near the 0.95 bar on every replica (baseline medians 0.936–0.954), so conditioning retains as little as 29% of rounds in one post-kill window; the unconditioned means agree with the conditioned ones.
