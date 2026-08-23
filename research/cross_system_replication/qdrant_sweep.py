"""
Run the Qdrant cross-system experiment across several seeds, in both
conditions -- the direct analog of ../replica_recall/sweep.py, addressing
PR #6 review item 1 (a matched-scale baseline/chaos pair per seed, not the
mismatched single-seed pilot).

Results land in results_sweep/seed<S>_<condition>/ and are read back by
../replica_recall/aggregate.py -- reused UNMODIFIED, since qdrant_run_
experiment.py writes the identical samples.csv/run_meta.json schema
run_experiment.py does. Naming convention (seed<N>_(baseline|chaos|quiesce))
matches aggregate.py's RUN_RE exactly.

Usage:
    python research/cross_system_replication/qdrant_sweep.py --seeds 5
    python research/cross_system_replication/qdrant_sweep.py --seeds 5 --with-quiesce

Resumable: a run whose output directory already contains samples.csv is
skipped. Pass --force to redo everything.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SWEEP_DIR = os.path.join(HERE, "results_sweep")
RESULTS_DIR = os.path.join(HERE, "results")
RUNNER = os.path.join(HERE, "qdrant_run_experiment.py")


def run_one(seed: int, cond: str, duration: int, writers: int,
            chaos_duration: int, pre_chaos_s: float, extra: list[str],
            force: bool) -> tuple[bool, str]:
    dest = os.path.join(SWEEP_DIR, f"seed{seed}_{cond}")

    if os.path.exists(os.path.join(dest, "samples.csv")) and not force:
        return True, f"seed {seed} {cond:<8} SKIP (already present)"

    cmd = [sys.executable, RUNNER,
           "--duration", str(duration),
           "--writers", str(writers),
           "--seed", str(seed)]
    if cond == "baseline":
        cmd.append("--no-chaos")
    elif cond == "quiesce":
        cmd += ["--chaos-duration", str(chaos_duration),
               "--pre-chaos-s", str(pre_chaos_s)]
    # cond == "chaos": faults for the whole duration, no extra flags.
    cmd += extra

    shutil.rmtree(RESULTS_DIR, ignore_errors=True)

    t0 = time.time()
    proc = subprocess.run(cmd, cwd=os.path.dirname(os.path.dirname(HERE)),
                          capture_output=True, text=True)
    dt = time.time() - t0

    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-5:]
        return False, (f"seed {seed} {cond:<8} FAILED (exit {proc.returncode}) "
                       f"{' | '.join(tail)}")

    if not os.path.exists(os.path.join(RESULTS_DIR, "samples.csv")):
        return False, f"seed {seed} {cond:<8} FAILED (no samples.csv produced)"

    shutil.rmtree(dest, ignore_errors=True)
    shutil.move(RESULTS_DIR, dest)

    n = sum(1 for _ in open(os.path.join(dest, "samples.csv"))) - 1
    return True, f"seed {seed} {cond:<8} ok  {n:>4} samples  {dt:5.0f}s"


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seeds", type=int, default=5)
    ap.add_argument("--seed-base", type=int, default=20260808)
    ap.add_argument("--duration", type=int, default=180)
    ap.add_argument("--writers", type=int, default=4)
    ap.add_argument("--only", choices=("baseline", "chaos", "quiesce"),
                    default=None)
    ap.add_argument("--with-quiesce", action="store_true")
    ap.add_argument("--chaos-duration", type=int, default=90)
    ap.add_argument("--pre-chaos-s", type=float, default=30.0)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--out-dir", default=None)
    args, extra = ap.parse_known_args()

    global SWEEP_DIR
    if args.out_dir:
        SWEEP_DIR = (args.out_dir if os.path.isabs(args.out_dir)
                     else os.path.join(HERE, args.out_dir))
    os.makedirs(SWEEP_DIR, exist_ok=True)
    seeds = [args.seed_base + i for i in range(args.seeds)]
    if args.only:
        conditions = [args.only]
    else:
        conditions = ["baseline", "chaos"]
        if args.with_quiesce:
            conditions.append("quiesce")

    total = len(seeds) * len(conditions)
    est_min = total * (args.duration + 40) / 60.0
    print(f"[qsweep] {len(seeds)} seeds x {len(conditions)} conditions "
          f"= {total} runs, ~{est_min:.0f} min")
    print(f"[qsweep] output: {SWEEP_DIR}")
    print()

    failures = 0
    done = 0
    for seed in seeds:
        for cond in conditions:
            ok, msg = run_one(seed, cond, args.duration, args.writers,
                              args.chaos_duration, args.pre_chaos_s, extra,
                              args.force)
            done += 1
            print(f"  [{done}/{total}] {msg}", flush=True)
            if not ok:
                failures += 1

    print()
    if failures:
        print(f"[qsweep] {failures}/{total} runs FAILED")
    print(f"[qsweep] aggregate with:\n"
          f"  python research/replica_recall/aggregate.py --sweep-dir {SWEEP_DIR}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
