# cross_system_replication

Implementation for the research question in [`SPEC.md`](SPEC.md): does
nano-db's replica-recall divergence result generalize to Qdrant?

Read `SPEC.md` first -- this is a pointer to the code, not a second copy of
the spec.

## Files

- `proto/` -- vendored Qdrant internal gRPC `.proto` files (not shipped by
  `qdrant-client`) that make the direct-per-replica probe possible. See
  `proto/README.md` and SPEC.md's 2026-08-23 addendum.
- `qdrant_probe.py` -- direct per-replica gRPC probe (`probe.py`'s analog).
- `qdrant_topology.py` -- cluster topology, port layout, Docker Compose
  generation, REST helpers (`chaos_harness.py`'s topology section's analog).
- `qdrant_docker_harness.py` -- Docker container kill/restart chaos loop
  and Raft split-brain validator (`chaos_harness.py`'s analog).
- `qdrant_run_experiment.py` -- the experiment runner
  (`run_experiment.py`'s analog). Reuses `../replica_recall/metrics.py`
  and `../replica_recall/sift.py` unmodified.
- `results/` -- pilot run outputs. See SPEC.md's Results section for what
  each file is and, importantly, what it is *not* (a completed seed sweep).

## Running it

```
pip install grpcio grpcio-tools numpy
# Docker Desktop (or another Docker daemon) must be running.
python qdrant_run_experiment.py --dist sift --sift-vectors 100000 \
    --duration 150 --warmup-s 15 --chaos-duration 60 --pre-chaos-s 20
```

`--no-chaos` for a baseline run. `--dist uniform` for a fast, no-download
smoke test of the harness itself (what `results/*uniform*` numbers, if
present, came from). See `qdrant_run_experiment.py --help` for the rest of
the protocol knobs, which mirror `run_experiment.py`'s.

Analysis: this branch does not yet have a `qdrant`-specific `analyze.py`
(see SPEC.md's Decision) -- the CSV/JSON schema is identical to
`../replica_recall/run_experiment.py`'s output, so `../replica_recall/analyze.py`
is the starting point once results directories are pointed at each other,
but has not been adapted or run against Qdrant output on this branch.

## Running a sweep

**Use `qdrant_sweep.py`.** It clears `results/` before each run, checks the exit
status, checks `samples.csv` was actually produced, and *moves* rather than
copies -- so a failed run yields a `FAILED` line and no directory, instead of
leaving the previous run's output to be picked up as fresh.

That matters more than it sounds. During #24 a hand-written bash loop was used
instead, a dead Docker daemon failed all 15 runs, and the loop copied one stale
predecessor 15 times into 15 differently-named directories. None of the 15
fabricated directories was committed, but the data was internally consistent and
looked real; it was caught only because identical inputs produced identical
outputs. The sweep tool would have refused at the first run.

**Scratch output is untracked by design.** `results/samples.csv`, `events.json`,
`run_meta.json` and `telemetry.csv` are in `.gitignore`. They were not always:
PR #18 and PR #24 each committed the last run's scratch output as a side-effect,
so `main` carried an unlabeled stale run in this directory for two merges --
the same hazard as above, sitting in the validated research state (found in
PR #27's review; the deleted copy was byte-identical to
`../kill_spacing_corrected/results/short-gap-same-node_seed20260950/`). Evidence
belongs in a *named* directory, moved there deliberately -- which is what
`qdrant_sweep.py` does, and what the `*_pilot*` files beside the scratch paths
are.

If a sweep needs conditions `qdrant_sweep.py` does not express -- its own are the
fixed `baseline`/`chaos`/`quiesce` triple -- extend it rather than replacing it
with a loop. It already forwards unrecognised flags to
`qdrant_run_experiment.py` via `parse_known_args`.

For a single run, `qdrant_run_experiment.py --out-dir DIR` writes that run's
output to its own directory, and every `run_meta.json` carries `run_id`,
`started_at` and `argv` so output can be checked against what was requested
without remembering it.
