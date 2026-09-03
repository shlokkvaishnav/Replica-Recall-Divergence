# Indexing gate for the Qdrant harness

Issue #28 · branch `method/qdrant-index-gate` · instrument for README open question #1, step 1.

**Question.** Can the harness guarantee the corpus is HNSW-indexed on every replica *before* the baseline clock starts, and keep it indexed through the window, so `index_recall` measures a graph rather than an exact scan (`../qdrant_optimizer_masking/`)?

**Answer, in one line.** Only by lowering Qdrant's `indexing_threshold` to 1,000 KB; at the default (20,000 KB) and at 5,000 KB every replica plateaus at 85–93% indexed and stays there. Even at 1,000 KB the window is *mostly* indexed (0.87–0.98, around 0.93), not ≥95%. In the one chaos run, restarts recovered in 5–17s. Full results, interpretation, and the three dated amendments: [`SPEC.md`](SPEC.md).

## What was built

In `../cross_system_replication/`:

- `qdrant_index_gate.py` — a pure `gate_decision()` over poll history (every replica reachable, `status green`, `indexed_vectors_count >= (1 - tol) * points_count` for N consecutive polls; 0/0 is *not* indexed) and `wait_for_index_gate()`, which polls `/collections/{name}` and returns a record for `run_meta.json`.
- `qdrant_run_experiment.py` — `--index-gate`, `--index-gate-tol`, `--index-gate-consecutive`, `--index-gate-timeout`: pause the writers after warmup, block until the gate closes, *then* start the sampler. A gate that never closes fails the run (exit 3, `index_gate_failed.json`, no `samples.csv`) so a sweep records `FAILED` instead of measuring an exact scan. `--warmup-until-written N`: extend the warmup until N confirmed writes, so the gated corpus is a measured size (exit 4 if the pool runs dry). `--indexing-threshold-kb`: pass-through to `optimizers_config` at collection creation. All off by default; `run_meta.json` records `index_gate`, `indexing_threshold_kb`, `warmup_until_written`, `written_at_gate` either way.
- `qdrant_topology.create_collection(indexing_threshold_kb=None)`.

Here:

- `test_index_gate.py` — 14 checks on the decision, no cluster needed.
- `sweep_gate.py` — the 14-cell pilot with `qdrant_sweep.py`'s guards (per-run `--out-dir`, exit check, `samples.csv` check, `run_meta` verified against the request). A failed gate is kept as a labelled result.
- `analyze_gate.py` — gate-close time, indexed-fraction-in-window at the bar and at 1.0, failed-gate mode (`never-indexed` vs `plateau@f`), probe cost, re-index time after each restart. Committed before the sweep ran.
- `results/` — one directory per cell; `analysis_output.txt` is the analyzer's output; `OBSERVATION_during_sweep.md` is the read-only look at the live collection that explained the plateau mechanism.
- `discarded_uncontrolled_corpus/` — the first cell, gated on 66.8k points under a "100k" label before `--warmup-until-written` existed. Kept, excluded (Amendment 2).

## Why the tail exists

`default_segment_number` auto → two segments per shard, one of them appendable. Everything written since the last merge lives there, un-indexed until *that segment alone* exceeds `indexing_threshold`. Why it is not merged into the indexed segment was not observed directly, but is consistent with Qdrant's documented merge optimizer, which acts only when the segment count exceeds its target. So the un-indexed fraction is write-phase-sized, not fixed — it does not shrink with a bigger corpus — and only a threshold small enough for the appendable segment to cross it on its own does anything. `results/OBSERVATION_during_sweep.md` has the raw read. (An earlier draft also claimed `full_scan_threshold` forces exact scan of that segment; that parameter governs payload-filtered search only and the claim was withdrawn in review.)

## Reproducing

```bash
python research/qdrant_index_gate/test_index_gate.py          # decision logic, seconds
python research/qdrant_index_gate/sweep_gate.py --dry-run     # the 14 cells
python research/qdrant_index_gate/sweep_gate.py               # ~2h on one host
python research/qdrant_index_gate/analyze_gate.py             # the table in SPEC.md
```

One host, one image digest, two repeats per cell — timings are ranges, not distributions.
