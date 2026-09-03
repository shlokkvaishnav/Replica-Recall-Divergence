#!/usr/bin/env python3
"""
Sweep driver for the indexing-gate pilot (issue #28).

SPEC.md's design: {default, 5000, 1000} KB indexing threshold x {100k, 200k}
vectors, 2 --no-chaos runs per cell, plus 1 chaos run at (default, 200k) to
check outcome (iii). 13 runs; SPEC.md says 15 because it counted the chaos
run per threshold -- one is enough to answer (iii) and the other two would
only matter if (iii) fails, which is recorded as an amendment in SPEC.md.

Guards, inherited from ../cross_system_replication/qdrant_sweep.py because
#24 was bitten by not using them: every run gets its own --out-dir (so a
failed run leaves nothing, #26), exit status is checked, samples.csv must
exist, and run_meta.json's run_id/seed/argv are verified against what was
asked before the run counts. A gate that never closes exits 3 and leaves
index_gate_failed.json -- that is a RESULT for this pilot (outcome (c)), so
it is kept, labelled, and reported rather than discarded.

Usage:
    python research/qdrant_index_gate/sweep_gate.py [--dry-run] [--only CELL]
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
RUNNER = os.path.join(ROOT, "research", "cross_system_replication", "qdrant_run_experiment.py")
RESULTS = os.path.join(HERE, "results")

THRESHOLDS = [None, 5000, 1000]          # None = Qdrant default (20000 KB)
CORPORA = [100_000, 200_000]
REPEATS = 2
SEED0 = 20260970

# Held constant across every cell (SPEC.md): protocol write rate (4 writers,
# batch 32), --queries 10 (PR #25 showed completeness does not depend on it
# and probe cost does), duration long enough for the optimizer to show
# whether it keeps up once writers resume.
COMMON = ["--writers", "4", "--batch-size", "32", "--queries", "10",
          "--warmup-s", "40", "--duration", "120",
          "--index-gate", "--index-gate-tol", "0.05",   # SPEC.md Amendment 1
          "--index-gate-timeout", "600", "--capture-telemetry"]


def cells():
    seed = SEED0
    for thr in THRESHOLDS:
        for n in CORPORA:
            for rep in range(REPEATS):
                name = f"thr{thr if thr is not None else 'default'}_n{n // 1000}k_nochaos_seed{seed}"
                yield name, thr, n, seed, ["--no-chaos"]
                seed += 1
    # outcome (iii): one chaos run at default threshold, 200k
    name = f"thrdefault_n200k_chaos_seed{seed}"
    yield name, None, 200_000, seed, ["--chaos-duration", "60", "--pre-chaos-s", "30"]


def run_one(name, thr, n, seed, extra, dry):
    dest = os.path.join(RESULTS, name)
    if os.path.exists(os.path.join(dest, "run_meta.json")) or \
       os.path.exists(os.path.join(dest, "index_gate_failed.json")):
        return "SKIP (already present)"
    cmd = [sys.executable, RUNNER, "--seed", str(seed), "--sift-vectors", str(n),
           "--out-dir", dest] + COMMON + extra
    if thr is not None:
        cmd += ["--indexing-threshold-kb", str(thr)]
    if dry:
        return "DRY " + " ".join(cmd[2:])
    os.makedirs(dest, exist_ok=True)
    t0 = time.time()
    with open(os.path.join(dest, "stdout.log"), "w") as log:
        proc = subprocess.run(cmd, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, text=True)
    dt = time.time() - t0
    if proc.returncode == 3 and os.path.exists(os.path.join(dest, "index_gate_failed.json")):
        return f"GATE-FAILED (kept as a result)  {dt:5.0f}s"
    if proc.returncode != 0:
        return f"FAILED (exit {proc.returncode})  {dt:5.0f}s  see stdout.log"
    meta_p = os.path.join(dest, "run_meta.json")
    if not os.path.exists(os.path.join(dest, "samples.csv")) or not os.path.exists(meta_p):
        return f"FAILED (no samples.csv/run_meta.json)  {dt:5.0f}s"
    meta = json.load(open(meta_p))
    if meta.get("seed") != seed or meta.get("sift_vectors") != n or \
       meta.get("indexing_threshold_kb") != thr:
        return (f"FAILED (run_meta mismatch: seed {meta.get('seed')} vectors "
                f"{meta.get('sift_vectors')} thr {meta.get('indexing_threshold_kb')})")
    g = meta.get("index_gate") or {}
    return f"ok  gate {g.get('elapsed_s')}s  {dt:5.0f}s"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--only", default=None, help="substring filter on cell name")
    a = ap.parse_args()
    os.makedirs(RESULTS, exist_ok=True)
    for name, thr, n, seed, extra in cells():
        if a.only and a.only not in name:
            continue
        print(f"{name:<44} ", end="", flush=True)
        print(run_one(name, thr, n, seed, extra, a.dry_run), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
