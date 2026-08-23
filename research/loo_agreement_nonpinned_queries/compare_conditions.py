"""
The actual pre-registered comparison for issue #5: pinned vs. nonpinned
loo_agreement detection, across independent seeds -- not the within-run
statistic the single-seed pilot in SPEC.md reported (that one compares
correlated samples along one chaos trajectory; this one compares 5
independent seed-level summaries per condition, per SPEC.md's Metrics
section).

Reuses ../replica_recall/analyze.py's summarize_run() and aggregate.py's
mann_whitney() unmodified -- both sweeps produce the same samples.csv/
run_meta.json schema regardless of --loo-query-mode.

Usage:
    python research/loo_agreement_nonpinned_queries/compare_conditions.py \
        --pinned results_sweep_loo_pinned --nonpinned results_sweep_loo_nonpinned
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


def per_seed_chaos_stats(sweep_dir: str) -> list[tuple[int, dict]]:
    runs = discover(sweep_dir)
    return sorted(runs["chaos"])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pinned", default=os.path.join(HERE, "results_sweep_loo_pinned"))
    ap.add_argument("--nonpinned", default=os.path.join(HERE, "results_sweep_loo_nonpinned"))
    args = ap.parse_args()

    pinned = per_seed_chaos_stats(args.pinned)
    nonpinned = per_seed_chaos_stats(args.nonpinned)

    print(f"\npinned seeds:    {[s for s, _ in pinned]}")
    print(f"nonpinned seeds: {[s for s, _ in nonpinned]}")

    print("\n" + "=" * 78)
    print("Per-seed loo_agreement detection stats (chaos-condition runs only)")
    print("=" * 78)
    print(f"  {'seed':>10} {'cond':<10} {'hit_rate':>10} {'rank_corr':>10} {'margin':>10}")
    for seed, s in pinned:
        print(f"  {seed:>10} {'pinned':<10} {s['hit_rate']:>10.4f} "
              f"{s['rank_corr']:>10.4f} {s['margin']:>10.4f}")
    for seed, s in nonpinned:
        print(f"  {seed:>10} {'nonpinned':<10} {s['hit_rate']:>10.4f} "
              f"{s['rank_corr']:>10.4f} {s['margin']:>10.4f}")

    print("\n" + "=" * 78)
    print("Between-condition comparison: pinned vs nonpinned, 5 seeds each")
    print("(the actual pre-registered test -- SPEC.md's Metrics section)")
    print("=" * 78)

    for key, label in [("hit_rate", "detection hit rate"),
                       ("rank_corr", "rank correlation"),
                       ("margin", "true-recall margin")]:
        p_vals = [s[key] for _, s in pinned]
        n_vals = [s[key] for _, s in nonpinned]
        u, p = mann_whitney(p_vals, n_vals)
        star = "*" if p <= 0.05 else " "
        print(f"  {label:<22} pinned {np.mean(p_vals):.4f} +/- "
              f"{np.std(p_vals, ddof=1):.4f}   nonpinned {np.mean(n_vals):.4f} "
              f"+/- {np.std(n_vals, ddof=1):.4f}   p={p:.4f}{star}")

    print("\n  Exact two-sided Mann-Whitney, 5v5, floor p=0.0079.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
