"""
Decision item 2: does the 5-seed sweep's healing variance (recovery 84%,
0%, 25%, -32%, 100%) correlate with anything in the already-collected
events.json/run_meta.json per quiesce run? Analysis only -- no new runs.

Checks, per seed: chaos event count, whether any node was killed twice,
the shortest same-node recovery gap (time between a node coming back up
and being killed again), total confirmed writes, and write failure rate.

Usage:
    python research/cross_system_replication/analyze_healing_variance.py
"""

from __future__ import annotations

import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
SWEEP_DIR = os.path.join(HERE, "results_sweep")
SEEDS = [20260910, 20260911, 20260912, 20260913, 20260914]

# From aggregate.py's healing table for this sweep (recomputed here would
# require re-deriving heal_stats(); the numbers are copied from the
# already-committed aggregate output rather than re-run, since they are not
# what's under test here).
RECOVERED_PCT = {20260910: 84, 20260911: 0, 20260912: 25,
                 20260913: -32, 20260914: 100}


def main() -> int:
    print(f"{'seed':>10} {'recovered':>10} {'n_events':>9} {'repeat_node':>12} "
          f"{'min_gap_s':>10} {'confirmed':>10} {'fail_rate':>10}")
    for seed in SEEDS:
        qdir = os.path.join(SWEEP_DIR, f"seed{seed}_quiesce")
        meta = json.load(open(os.path.join(qdir, "run_meta.json")))
        events = json.load(open(os.path.join(qdir, "events.json")))

        by_node: dict[str, list[dict]] = {}
        for e in events:
            by_node.setdefault(e["target"], []).append(e)
        repeats = {n: es for n, es in by_node.items() if len(es) > 1}

        min_gap = None
        for _n, es in repeats.items():
            es = sorted(es, key=lambda e: e["t_rel"])
            for i in range(len(es) - 1):
                recover_t = es[i]["t_rel"] + es[i].get("down_for_s", 0)
                gap = es[i + 1]["t_rel"] - recover_t
                if min_gap is None or gap < min_gap:
                    min_gap = gap

        fail_rate = (meta["write_failed"] / meta["write_attempted"]
                    if meta["write_attempted"] else 0.0)
        print(f"{seed:>10} {RECOVERED_PCT[seed]:>9}% {len(events):>9} "
              f"{str(bool(repeats)):>12} "
              f"{(f'{min_gap:.1f}' if min_gap is not None else 'n/a'):>10} "
              f"{meta['confirmed_total']:>10} {fail_rate:>9.1%}")

    print("\nSee SPEC.md's 2026-08-23 (later still) addendum for interpretation --")
    print("the shortest-recovery-gap seed (20260913) is also the worst outcome and")
    print("the highest write-failure-rate seed, but this does not rank the other")
    print("four seeds cleanly. Narrowed candidate, not a confirmed mechanism.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
