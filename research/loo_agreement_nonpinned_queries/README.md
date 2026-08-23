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
**Read SPEC.md's addendum before trusting these pilot numbers -- the
headline finding did not survive the 5-seed sweep below.**

## The 5-seed sweep

```
python research/replica_recall/sweep.py --seeds 5 --seed-base 20260900 \
    --duration 90 --writers 4 --out-dir results_sweep_loo_pinned \
    --dist sift --sift-vectors 100000 --queries 100 --k 10 --warmup-s 10 \
    --sample-interval 5 --loo-query-mode pinned

python research/replica_recall/sweep.py --seeds 5 --seed-base 20260900 \
    --duration 90 --writers 4 --out-dir results_sweep_loo_nonpinned \
    --dist sift --sift-vectors 100000 --queries 100 --k 10 --warmup-s 10 \
    --sample-interval 5 --loo-query-mode nonpinned --loo-queries 100 --loo-pool-size 3000
```

`../replica_recall/sweep.py` is reused completely unmodified -- it forwards
unrecognized flags straight through to `run_experiment.py`. Results are
`results_sweep_loo_pinned/` and `results_sweep_loo_nonpinned/`.

## Analysis

`research/replica_recall/analyze.py` and `aggregate.py` work unmodified on
either condition's output -- the CSV/directory schema didn't change, only
how the `loo_agreement` column's values were computed. `aggregate.py
--sweep-dir results_sweep_loo_pinned` (or `_nonpinned`) gives each
condition's own baseline-vs-chaos comparison (output saved as
`results/aggregate_{pinned,nonpinned}.txt`). For the actual research
question -- pinned vs. nonpinned, not baseline vs. chaos -- use
`compare_conditions.py` (this branch), which extracts each condition's
5 chaos-run seed-level summaries and runs the between-condition
Mann-Whitney the issue's spec calls for. See SPEC.md's addendum for the
result and what it means.
