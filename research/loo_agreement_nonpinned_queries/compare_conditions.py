"""
The actual pre-registered comparison for issue #5: pinned vs. nonpinned
loo_agreement detection, across independent seeds -- not the within-run
statistic the single-seed pilot in SPEC.md reported (that one compares
correlated samples along one chaos trajectory; this one compares 5
independent seed-level summaries per condition, per SPEC.md's Metrics
section).

Reuses ../replica_recall/analyze.py's summarize_run() and aggregate.py's
mann_whitney() unmodified -- every sweep produces the same samples.csv/
run_meta.json schema regardless of --loo-query-mode/--loo-queries.

Takes an arbitrary set of named conditions (not just two), so the same
tool covers both the original pinned-vs-nonpinned comparison and the
follow-up --loo-queries sweep (a third, more aggressive condition) without
a second script. Compares every pair.

Usage:
    python research/loo_agreement_nonpinned_queries/compare_conditions.py \
        --condition pinned=results_sweep_loo_pinned \
        --condition nonpinned=results_sweep_loo_nonpinned \
        --condition nonpinned_small=results_sweep_loo_nonpinned_small
"""

from __future__ import annotations

import argparse
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
RR = os.path.join(os.path.dirname(HERE), "replica_recall")
sys.path.insert(0, RR)

from aggregate import discover, mann_whitney                      # noqa: E402

DEFAULT_CONDITIONS = [
    ("pinned", os.path.join(HERE, "results_sweep_loo_pinned")),
    ("nonpinned", os.path.join(HERE, "results_sweep_loo_nonpinned")),
]


def per_seed_chaos_stats(sweep_dir: str) -> list[tuple[int, dict]]:
    runs = discover(sweep_dir)
    return sorted(runs["chaos"])


def parse_condition(s: str) -> tuple[str, str]:
    name, _, path = s.partition("=")
    if not path:
        raise argparse.ArgumentTypeError(f"expected name=path, got {s!r}")
    return name, path


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--condition", action="append", type=parse_condition,
                    dest="conditions",
                    help="name=path/to/sweep_dir, repeatable. Default: the "
                         "original pinned/nonpinned pair.")
    args = ap.parse_args()

    conditions = args.conditions or DEFAULT_CONDITIONS
    data = {name: per_seed_chaos_stats(path) for name, path in conditions}

    print("\n" + "=" * 78)
    print("Per-seed loo_agreement detection stats (chaos-condition runs only)")
    print("=" * 78)
    print(f"  {'seed':>10} {'condition':<16} {'hit_rate':>10} {'rank_corr':>10} {'margin':>10}")
    for name, runs in data.items():
        print(f"  ({name}: seeds {[s for s, _ in runs]})")
        for seed, s in runs:
            print(f"  {seed:>10} {name:<16} {s['hit_rate']:>10.4f} "
                  f"{s['rank_corr']:>10.4f} {s['margin']:>10.4f}")

    print("\n" + "=" * 78)
    print("Between-condition comparisons, every pair (5 seeds each)")
    print("(the actual pre-registered test -- SPEC.md's Metrics section)")
    print("=" * 78)

    names = list(data.keys())
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            a, b = names[i], names[j]
            print(f"\n  {a}  vs  {b}")
            for key, label in [("hit_rate", "detection hit rate"),
                               ("rank_corr", "rank correlation"),
                               ("margin", "true-recall margin")]:
                av = [s[key] for _, s in data[a]]
                bv = [s[key] for _, s in data[b]]
                u, p = mann_whitney(av, bv)
                star = "*" if p <= 0.05 else " "
                print(f"    {label:<20} {a} {np.mean(av):.4f} +/- "
                      f"{np.std(av, ddof=1):.4f}   {b} {np.mean(bv):.4f} "
                      f"+/- {np.std(bv, ddof=1):.4f}   p={p:.4f}{star}")

    print("\n  Exact two-sided Mann-Whitney, 5v5, floor p=0.0079.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
