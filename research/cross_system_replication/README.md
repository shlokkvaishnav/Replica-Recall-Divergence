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
