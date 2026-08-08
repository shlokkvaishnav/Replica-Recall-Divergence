"""
Turn samples.csv into the four answers the experiment exists to produce.

  Q1  Do replicas of the same shard disagree at the same instant?
      -> spread of e2e_recall within a shard at each timestamp.

  Q2  Does recall drop around failover, and does it come back?
      -> metrics bucketed by time since the nearest chaos kill.

  Q3  When recall drops, is it the graph or the data?
      -> index_recall vs completeness. This is the decomposition; it is the
         thing no existing tool reports.

  Q4  Does ground-truth-free agreement track true recall?
      -> correlation of shard_agreement against mean e2e_recall. This is the
         Layer 3 premise. A strong correlation means you can detect a silent
         replica in production without ground truth. A weak one is also a
         result, and an important one.

Usage:
    python research/replica_recall/analyze.py
    python research/replica_recall/analyze.py --results-dir path/to/results
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))


def _f(row: dict, key: str) -> float:
    v = row.get(key, "")
    if v == "" or v is None:
        return float("nan")
    try:
        return float(v)
    except ValueError:
        return float("nan")


def load(results_dir: str):
    csv_path = os.path.join(results_dir, "samples.csv")
    if not os.path.exists(csv_path):
        print(f"ERROR: {csv_path} not found. Run run_experiment.py first.",
              file=sys.stderr)
        sys.exit(1)
    with open(csv_path, newline="") as f:
        rows = list(csv.DictReader(f))

    events = []
    ev_path = os.path.join(results_dir, "events.json")
    if os.path.exists(ev_path):
        with open(ev_path) as f:
            events = json.load(f)

    meta = {}
    meta_path = os.path.join(results_dir, "run_meta.json")
    if os.path.exists(meta_path):
        with open(meta_path) as f:
            meta = json.load(f)

    return rows, events, meta


def nanmean(xs) -> float:
    xs = [x for x in xs if not np.isnan(x)]
    return float(np.mean(xs)) if xs else float("nan")


def fmt(x: float, nd: int = 4) -> str:
    return "  n/a " if np.isnan(x) else f"{x:.{nd}f}"


# ---------------------------------------------------------------------------

def q1_intra_shard_spread(rows) -> None:
    print("\n" + "=" * 72)
    print("Q1  Do replicas of the same shard disagree at the same instant?")
    print("=" * 72)

    by_ts: dict[tuple[str, str], list[float]] = {}
    for r in rows:
        if r["reachable"] != "1":
            continue
        v = _f(r, "e2e_recall")
        if np.isnan(v):
            continue
        by_ts.setdefault((r["t_rel"], r["shard"]), []).append(v)

    spreads, mins, maxes = [], [], []
    for (_, _), vals in by_ts.items():
        if len(vals) < 2:
            continue
        spreads.append(max(vals) - min(vals))
        mins.append(min(vals))
        maxes.append(max(vals))

    if not spreads:
        print("  no instants with >=2 reachable replicas of the same shard")
        return

    spreads_a = np.asarray(spreads)
    print(f"  instants compared      : {len(spreads)}")
    print(f"  mean within-shard spread: {fmt(float(spreads_a.mean()))}")
    print(f"  p95 spread              : {fmt(float(np.percentile(spreads_a, 95)))}")
    print(f"  max spread              : {fmt(float(spreads_a.max()))}")
    print(f"  worst replica (mean)    : {fmt(nanmean(mins))}")
    print(f"  best  replica (mean)    : {fmt(nanmean(maxes))}")
    print()
    print("  Interpretation: a non-trivial spread means two replicas of the")
    print("  same shard answered the same query set differently at the same")
    print("  moment -- invisible to any client, which sees only one of them.")


def q2_recall_around_failover(rows, events) -> None:
    print("\n" + "=" * 72)
    print("Q2  Does recall drop around failover, and does it recover?")
    print("=" * 72)

    if not events:
        print("  no chaos events (baseline run) -- skipping")
        return

    kills_by_target: dict[str, list[float]] = {}
    for e in events:
        kills_by_target.setdefault(e["target"], []).append(e["t_rel"])

    buckets = [(-1e9, 0, "before any kill"), (0, 5, "0-5s after kill"),
               (5, 15, "5-15s after"), (15, 30, "15-30s after"),
               (30, 1e9, ">30s after")]
    acc: dict[str, dict[str, list[float]]] = {
        b[2]: {"e2e": [], "index": [], "compl": []} for b in buckets}

    for r in rows:
        if r["reachable"] != "1":
            continue
        t = _f(r, "t_rel")
        ks = kills_by_target.get(r["name"], [])
        prior = [k for k in ks if k <= t]
        dt = (t - max(prior)) if prior else -1e9
        for lo, hi, label in buckets:
            if lo <= dt < hi:
                acc[label]["e2e"].append(_f(r, "e2e_recall"))
                acc[label]["index"].append(_f(r, "index_recall"))
                acc[label]["compl"].append(_f(r, "completeness"))
                break

    print(f"  {'window':<18} {'n':>5} {'e2e':>8} {'index':>8} {'complete':>9}")
    print("  " + "-" * 52)
    for _, _, label in buckets:
        a = acc[label]
        n = len([x for x in a["e2e"] if not np.isnan(x)])
        if n == 0:
            continue
        print(f"  {label:<18} {n:>5} {fmt(nanmean(a['e2e'])):>8} "
              f"{fmt(nanmean(a['index'])):>8} {fmt(nanmean(a['compl'])):>9}")
    print()
    print("  Interpretation: recovery means the later windows return to the")
    print("  'before any kill' level. A permanent step down means the kill")
    print("  left the replica degraded -- the failure mode worth a paper.")


def q3_graph_vs_data(rows) -> None:
    print("\n" + "=" * 72)
    print("Q3  When recall drops, is it the graph or the data?")
    print("=" * 72)

    idx, comp, e2e = [], [], []
    for r in rows:
        if r["reachable"] != "1":
            continue
        i, c, e = (_f(r, "index_recall"), _f(r, "completeness"),
                   _f(r, "e2e_recall"))
        if not (np.isnan(i) or np.isnan(c)):
            idx.append(i)
            comp.append(c)
            e2e.append(e)

    if not idx:
        print("  no scored samples")
        return

    print(f"  samples                 : {len(idx)}")
    print(f"  mean index_recall       : {fmt(nanmean(idx))}   (graph quality)")
    print(f"  mean completeness       : {fmt(nanmean(comp))}   (data content)")
    print(f"  mean e2e_recall         : {fmt(nanmean(e2e))}   (what a client sees)")
    print()

    worst = sorted(zip(e2e, idx, comp), key=lambda x: (np.isnan(x[0]), x[0]))[:5]
    print("  worst 5 samples by e2e_recall:")
    print(f"    {'e2e':>8} {'index':>8} {'complete':>9}   verdict")
    for e, i, c in worst:
        verdict = ("graph degraded" if (not np.isnan(i) and i < c - 0.05)
                   else "data missing" if (not np.isnan(c) and c < i - 0.05)
                   else "both / neither")
        print(f"    {fmt(e):>8} {fmt(i):>8} {fmt(c):>9}   {verdict}")


def q4_agreement_vs_truth(rows) -> None:
    print("\n" + "=" * 72)
    print("Q4  Does ground-truth-free agreement track true recall?")
    print("=" * 72)

    by_ts: dict[tuple[str, str], dict] = {}
    for r in rows:
        if r["reachable"] != "1":
            continue
        key = (r["t_rel"], r["shard"])
        slot = by_ts.setdefault(key, {"agree": float("nan"), "e2e": []})
        a = _f(r, "shard_agreement")
        if not np.isnan(a):
            slot["agree"] = a
        v = _f(r, "e2e_recall")
        if not np.isnan(v):
            slot["e2e"].append(v)

    xs, ys = [], []
    for slot in by_ts.values():
        if np.isnan(slot["agree"]) or not slot["e2e"]:
            continue
        xs.append(slot["agree"])
        ys.append(float(np.mean(slot["e2e"])))

    if len(xs) < 3:
        print(f"  only {len(xs)} paired observations -- need more samples")
        return

    x, y = np.asarray(xs), np.asarray(ys)
    if x.std() < 1e-9 or y.std() < 1e-9:
        print(f"  paired observations     : {len(xs)}")
        print("  correlation undefined (one variable is constant)")
        print(f"  mean agreement          : {fmt(float(x.mean()))}")
        print(f"  mean e2e_recall         : {fmt(float(y.mean()))}")
        print()
        print("  A constant series usually means the run was too quiet.")
        print("  Increase --duration, or lower --sample-interval.")
        return

    r_pearson = float(np.corrcoef(x, y)[0, 1])
    xr = np.argsort(np.argsort(x))
    yr = np.argsort(np.argsort(y))
    r_spearman = float(np.corrcoef(xr, yr)[0, 1])

    print(f"  paired observations     : {len(xs)}")
    print(f"  mean agreement          : {fmt(float(x.mean()))}")
    print(f"  mean e2e_recall         : {fmt(float(y.mean()))}")
    print(f"  Pearson  r              : {fmt(r_pearson)}")
    print(f"  Spearman r              : {fmt(r_spearman)}")
    print()
    if abs(r_spearman) >= 0.7:
        print("  STRONG: cross-replica agreement predicts true recall well.")
        print("  That is a production-viable detector for a currently silent")
        print("  failure -- the Layer 3 result.")
    elif abs(r_spearman) >= 0.4:
        print("  MODERATE: some signal. Worth checking whether it sharpens")
        print("  with more queries per sample or a longer run.")
    else:
        print("  WEAK: agreement does not track truth here. Also a result --")
        print("  it would mean replica divergence is NOT detectable by")
        print("  cross-replica comparison alone, and needs sentinel queries.")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--results-dir", default=os.path.join(HERE, "results"))
    args = ap.parse_args()

    rows, events, meta = load(args.results_dir)

    print("=" * 72)
    print("Replica recall divergence under failure -- Layer 1 results")
    print("=" * 72)
    if meta:
        print(f"  duration={meta.get('duration_s')}s  "
              f"queries={meta.get('queries')}  k={meta.get('k')}  "
              f"chaos={meta.get('chaos')}")
        print(f"  {meta.get('num_shards')} shards x "
              f"{meta.get('replicas_per_shard')} replicas   "
              f"confirmed={meta.get('confirmed_total')}  "
              f"seed={meta.get('seed')}")
    print(f"  rows={len(rows)}  chaos_events={len(events)}")

    unreachable = sum(1 for r in rows if r["reachable"] != "1")
    print(f"  unreachable samples: {unreachable}/{len(rows)} "
          f"({100.0 * unreachable / max(len(rows), 1):.1f}%)")

    q1_intra_shard_spread(rows)
    q2_recall_around_failover(rows, events)
    q3_graph_vs_data(rows)
    q4_agreement_vs_truth(rows)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
