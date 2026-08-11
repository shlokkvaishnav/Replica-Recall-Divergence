"""
Run the experiment across several seeds, in both conditions.

A single baseline run and a single chaos run are one observation each. The
difference between "a finding" and "an anecdote" is repetition: the seed
controls both the query set and the chaos kill schedule, so varying it
resamples the whole experiment.

Results land in results_sweep/seed<S>_<condition>/ and are read back by
aggregate.py.

Usage:
    python research/replica_recall/sweep.py --seeds 5
    python research/replica_recall/sweep.py --seeds 5 --duration 300
    python research/replica_recall/sweep.py --seeds 5 --only chaos

Resumable: a run whose output directory already contains samples.csv is
skipped, so an interrupted sweep can be restarted without redoing work.
Pass --force to redo everything.
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
RUNNER = os.path.join(HERE, "run_experiment.py")


def run_one(seed: int, cond: str, duration: int, writers: int,
            chaos_duration: int, extra: list[str],
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
        # faults for a window, then stopped -- the healing test
        cmd += ["--chaos-duration", str(chaos_duration)]
    cmd += extra

    # Each run starts from a clean results dir so a crashed run cannot leave
    # a stale samples.csv that the next one silently inherits.
    shutil.rmtree(RESULTS_DIR, ignore_errors=True)

    t0 = time.time()
    proc = subprocess.run(cmd, cwd=os.path.dirname(os.path.dirname(HERE)),
                          capture_output=True, text=True)
    dt = time.time() - t0

    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-3:]
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
    ap.add_argument("--seeds", type=int, default=5,
                    help="number of seeds (default 5)")
    ap.add_argument("--seed-base", type=int, default=20260808)
    ap.add_argument("--duration", type=int, default=300)
    ap.add_argument("--writers", type=int, default=4)
    ap.add_argument("--only", choices=("baseline", "chaos", "quiesce"),
                    default=None, help="run only one condition")
    ap.add_argument("--with-quiesce", action="store_true",
                    help="also run the healing test: faults for "
                         "--chaos-duration seconds, then stopped")
    ap.add_argument("--chaos-duration", type=int, default=120,
                    help="quiesce condition: length of the fault window "
                         "(default 120; the rest of --duration is recovery)")
    ap.add_argument("--force", action="store_true",
                    help="re-run even if results already exist")
    ap.add_argument("--out-dir", default=None,
                    help="where to write runs (default results_sweep/). Use a "
                         "separate directory per corpus distribution -- run "
                         "names carry only seed and condition, so a lowdim "
                         "sweep would otherwise overwrite a uniform one.")
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
    # +20s warmup and ~25s of startup/teardown per run.
    est_min = total * (args.duration + 45) / 60.0
    print(f"[sweep] {len(seeds)} seeds x {len(conditions)} conditions "
          f"= {total} runs, ~{est_min:.0f} min")
    print(f"[sweep] output: {SWEEP_DIR}")
    print()

    failures = 0
    done = 0
    for seed in seeds:
        for cond in conditions:
            ok, msg = run_one(seed, cond, args.duration, args.writers,
                              args.chaos_duration, extra, args.force)
            done += 1
            print(f"  [{done}/{total}] {msg}", flush=True)
            if not ok:
                failures += 1

    print()
    if failures:
        print(f"[sweep] {failures}/{total} runs FAILED -- aggregate will use "
              f"whatever succeeded, but check before trusting it")
    print(f"[sweep] aggregate with:\n"
          f"  python {os.path.join(HERE, 'aggregate.py')}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
