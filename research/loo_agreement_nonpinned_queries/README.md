# loo_agreement_nonpinned_queries

Implementation for the research question in [`SPEC.md`](SPEC.md): does
`loo_agreement`'s degraded-replica detection accuracy survive a non-pinned,
realistic query workload, or does it depend on query pinning?

Read `SPEC.md` first.

## What changed

`research/replica_recall/run_experiment.py` gained three new flags:
`--loo-query-mode {pinned,nonpinned}` (default `pinned`, a no-op --
behavior is unchanged from before this branch), `--loo-queries`, and
`--loo-pool-size`. `metrics.py`, `sift.py`, `chaos_harness.py`, and
`analyze.py` are all unmodified.

## Running it

```
pip install grpcio grpcio-tools numpy
cmake -B build -DCMAKE_BUILD_TYPE=Release -DNANODB_BUILD_CLUSTER=ON && cmake --build build -j

# pinned (existing behaviour, unchanged)
python research/replica_recall/run_experiment.py --dist sift --sift-vectors 100000 \
    --seed 20260900 --queries 100 --k 10 --duration 150 --warmup-s 15 \
    --chaos-duration 60 --pre-chaos-s 20

# nonpinned (this branch)
python research/replica_recall/run_experiment.py --dist sift --sift-vectors 100000 \
    --seed 20260900 --queries 100 --k 10 --duration 150 --warmup-s 15 \
    --chaos-duration 60 --pre-chaos-s 20 \
    --loo-query-mode nonpinned --loo-queries 100 --loo-pool-size 3000
```

`results/*_pinned_pilot_seed20260900.*` and `*_nonpinned_pilot_seed20260900.*`
are exactly the output of those two commands (built and run inside a Linux
container per `research/replica_recall/RESULTS.md`'s note that this harness
needs Linux + built cluster binaries -- it was not run natively on Windows).

## Analysis

`research/replica_recall/analyze.py` works unmodified on either condition's
`samples.csv` -- the CSV schema didn't change, only how the `loo_agreement`
column's values were computed. See SPEC.md's Results section for the
detection-stats comparison between the two pilot runs.
