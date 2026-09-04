#!/usr/bin/env python3
"""
The #30 sweep, pinned. A thin wrapper over ../cross_system_replication/
qdrant_sweep.py -- the repository's sweep tool, used rather than replaced
(cross_system_replication/README.md, "Running a sweep") -- that fixes the
flags SPEC.md pre-registers so the command that ran is the command in the
record.

  run0     one gated --no-chaos run with --score-at-gate: the before/after-
           gate index_recall spot-check (SPEC.md, instrument characterization).
           Outcome (e) stops here if the prediction fails.
  sweep    5 seeds x {baseline, chaos, quiesce}, PR #6's protocol, gate on.

Every run: 100k confirmed writes on a 250k pool, indexing_threshold 1000 KB,
gate tol 0.05, telemetry on. Output: results/seed<N>_<cond>/ (qdrant_sweep's
layout, so ../replica_recall/aggregate.py works unmodified) and results/run0/.

Usage:
    python research/qdrant_gated_index_recall/sweep.py run0
    python research/qdrant_gated_index_recall/sweep.py sweep [--force]
"""
from __future__ import annotations

import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
CSR = os.path.join(ROOT, "research", "cross_system_replication")
RESULTS = os.path.join(HERE, "results")

SEED_BASE, SEEDS = 20261000, 5
GATE_FLAGS = ["--warmup-until-written", "100000", "--sift-vectors", "250000",
              "--indexing-threshold-kb", "1000",
              "--index-gate", "--index-gate-tol", "0.05", "--index-gate-timeout", "600",
              "--capture-telemetry"]


def run0() -> int:
    out = os.path.join(RESULTS, "run0")
    os.makedirs(out, exist_ok=True)
    cmd = [sys.executable, os.path.join(CSR, "qdrant_run_experiment.py"),
           "--no-chaos", "--duration", "60", "--writers", "4",
           "--seed", str(SEED_BASE - 1), "--score-at-gate", "--out-dir", out] + GATE_FLAGS
    print(" ".join(cmd[1:]), flush=True)
    return subprocess.run(cmd, cwd=ROOT).returncode


def sweep(force: bool) -> int:
    # PR #6's addendum protocol: --duration 120 baseline/chaos, quiesce
    # pre 20 / chaos 50 / quiesce 50, 4 writers. qdrant_sweep forwards
    # unknown flags to the runner via parse_known_args.
    cmd = [sys.executable, os.path.join(CSR, "qdrant_sweep.py"),
           "--seeds", str(SEEDS), "--seed-base", str(SEED_BASE),
           "--duration", "120", "--writers", "4", "--with-quiesce",
           "--chaos-duration", "50", "--pre-chaos-s", "20",
           "--out-dir", RESULTS] + (["--force"] if force else []) + GATE_FLAGS
    print(" ".join(cmd[1:]), flush=True)
    return subprocess.run(cmd, cwd=ROOT).returncode


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode == "run0":
        sys.exit(run0())
    if mode == "sweep":
        sys.exit(sweep("--force" in sys.argv))
    print(__doc__)
    sys.exit(2)
