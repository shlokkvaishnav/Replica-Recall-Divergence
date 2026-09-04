#!/usr/bin/env python3
"""
The #35 runs, pinned. Wrapper over ../cross_system_replication/qdrant_sweep.py
(the repository's sweep tool -- used, not replaced) fixing SPEC.md's flags so
the command that ran is the command in the record.

  baseline   5 seeds x --no-chaos, --duration 240 (the range recovery is judged
             against; long, because the tail regrows during a long baseline)
  quiesce    5 seeds x pre-chaos 20s / chaos 50s / quiesce 180s (--duration 250)

Both: 100k confirmed writes on a 250k pool, indexing_threshold 1000 KB, gate
tol 0.05, telemetry on -- PR #31's protocol with only the quiesce length
changed. Output: results/seed<N>_<cond>/ (qdrant_sweep's layout).

Usage:
    python research/qdrant_index_recall_healing/sweep.py baseline
    python research/qdrant_index_recall_healing/sweep.py quiesce
    python research/qdrant_index_recall_healing/sweep.py all
"""
from __future__ import annotations

import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
CSR = os.path.join(ROOT, "research", "cross_system_replication")
RESULTS = os.path.join(HERE, "results")

SEED_BASE, SEEDS = 20261100, 5
GATE_FLAGS = ["--warmup-until-written", "100000", "--sift-vectors", "250000",
              "--indexing-threshold-kb", "1000",
              "--index-gate", "--index-gate-tol", "0.05", "--index-gate-timeout", "600",
              "--capture-telemetry"]


def run(cond: str) -> int:
    common = [sys.executable, os.path.join(CSR, "qdrant_sweep.py"),
              "--seeds", str(SEEDS), "--seed-base", str(SEED_BASE),
              "--writers", "4", "--out-dir", RESULTS, "--only", cond]
    if cond == "baseline":
        cmd = common + ["--duration", "240"] + GATE_FLAGS
    elif cond == "quiesce":
        cmd = common + ["--duration", "250", "--pre-chaos-s", "20",
                        "--chaos-duration", "50"] + GATE_FLAGS
    else:
        raise SystemExit(f"unknown condition {cond!r}")
    print(" ".join(cmd[1:]), flush=True)
    return subprocess.run(cmd, cwd=ROOT).returncode


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode in ("baseline", "quiesce"):
        sys.exit(run(mode))
    if mode == "all":
        rc = run("baseline")
        sys.exit(rc or run("quiesce"))
    print(__doc__)
    sys.exit(2)
