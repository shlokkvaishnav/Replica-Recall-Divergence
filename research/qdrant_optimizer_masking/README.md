# qdrant_optimizer_masking

Implementation for the research question in [`SPEC.md`](SPEC.md): is
Qdrant's background segment-merge/optimizer activity masking a real
`index_recall` divergence in the merged `cross_system_replication` sweep's
null result (issue #8)?

Read `SPEC.md` first — the actual finding turned out to be more
fundamental than the issue's original hypothesis (background repair
masking damage): the corpus mostly wasn't HNSW-indexed at all during the
measurement window, so `index_recall` was largely measuring flat/exact
search, not the approximate graph the metric is designed to interrogate.

## What changed

`research/cross_system_replication/qdrant_run_experiment.py` gained one
new flag: `--capture-telemetry` (off by default, does not affect
`samples.csv` or any other branch's behavior). When set, it polls every
node's `/collections/{name}` REST endpoint at the same cadence as the
probe sampler and writes `results/telemetry.csv` (`indexed_vectors_count`,
`segments_count`, `status`, `optimizer_status` per node per round).

Container logs were tried first and found uninformative — Qdrant's
optimizer/segment-merge activity does not appear in `docker logs` at any
`RUST_LOG` level tested (`info`, `info,collection=debug`,
`info,collection::collection_manager::optimizers=debug,segment=debug`).
The REST telemetry endpoint was the working instrument.

## Running it

```
pip install grpcio grpcio-tools numpy
# Docker Desktop must be running.
python research/cross_system_replication/qdrant_run_experiment.py \
    --dist sift --sift-vectors 100000 --queries 100 --k 10 \
    --duration 150 --warmup-s 20 --pre-chaos-s 30 --chaos-duration 60 \
    --seed 20260920 --capture-telemetry

python research/qdrant_optimizer_masking/analyze_indexing_lag.py \
    --results-dir research/cross_system_replication/results
```

`results/*_instrumented_seed20260920.*` are exactly the output of the
first command (copied out before the next run overwrites
`research/cross_system_replication/results/`). `results/*_seed20260921.*`
is a second run, same command with `--seed 20260921`, added per PR #11's
review to meet SPEC.md's "at least 2 fresh runs" minimum -- see SPEC.md's
second addendum for both runs' numbers side by side.

## Analysis

`analyze_indexing_lag.py` (this branch) buckets `index_recall` samples by
whether any node had begun indexing at the nearest telemetry timestamp,
and reports the first telemetry sample where indexing started. See
SPEC.md's Results section for the numbers and what they mean.
