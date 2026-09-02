"""
issue #8: is Qdrant's index_recall null explained by the corpus mostly not
being HNSW-indexed at all during the measurement window (a distinct,
more fundamental mechanism than "optimizer masks chaos damage after the
fact")? Correlates telemetry.csv's indexed_vectors_count against samples.csv's
index_recall by nearest sample round.

Usage:
    python research/qdrant_optimizer_masking/analyze_indexing_lag.py \
        --results-dir path/to/results
"""

from __future__ import annotations

import argparse
import csv
import os

import numpy as np


def load_rows(path: str) -> list[dict]:
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--results-dir", required=True)
    args = ap.parse_args()

    samples = load_rows(os.path.join(args.results_dir, "samples.csv"))
    telemetry = load_rows(os.path.join(args.results_dir, "telemetry.csv"))

    tele_by_t: dict[float, dict[int, int]] = {}
    for r in telemetry:
        t = float(r["t_rel"])
        tele_by_t.setdefault(t, {})[int(r["node"])] = int(r["indexed_vectors_count"])
    tele_times = sorted(tele_by_t)

    def nearest_indexed_frac(t_rel: float) -> float:
        """Fraction of nodes (out of those reporting at the nearest
        telemetry timestamp) with indexed_vectors_count > 0."""
        nearest = min(tele_times, key=lambda tt: abs(tt - t_rel))
        counts = tele_by_t[nearest]
        if not counts:
            return float("nan")
        return sum(1 for c in counts.values() if c > 0) / len(counts)

    rows_by_bucket: dict[str, list[float]] = {"unindexed": [], "partially_indexed": []}
    for r in samples:
        if r["reachable"] != "1" or not r["index_recall"]:
            continue
        ir = float(r["index_recall"])
        t_rel = float(r["t_rel"])
        frac = nearest_indexed_frac(t_rel)
        if np.isnan(frac):
            continue
        bucket = "unindexed" if frac == 0.0 else "partially_indexed"
        rows_by_bucket[bucket].append(ir)

    print("index_recall, grouped by whether ANY node had begun HNSW-indexing "
          "at the nearest telemetry timestamp:\n")
    for bucket, vals in rows_by_bucket.items():
        if not vals:
            print(f"  {bucket:<20} n=0")
            continue
        print(f"  {bucket:<20} n={len(vals):<4} mean={np.mean(vals):.4f} "
              f"min={min(vals):.4f} max={max(vals):.4f}")

    # First telemetry timestamp at which ANY node shows indexed_vectors_count > 0.
    first_indexed_t = None
    for t in tele_times:
        if any(c > 0 for c in tele_by_t[t].values()):
            first_indexed_t = t
            break
    print(f"\nFirst telemetry sample with any node indexed_vectors_count>0: "
          f"t={first_indexed_t}")
    print(f"Total telemetry samples: {len(tele_times)}, "
          f"span t={tele_times[0]:.1f} to t={tele_times[-1]:.1f}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
