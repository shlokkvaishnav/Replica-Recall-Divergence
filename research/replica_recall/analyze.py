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


def q0_drift(rows) -> None:
    """Is recall stable over the run, independent of faults?

    Q2 compares 'before any kill' against later windows, but the pre-kill
    samples are drawn from early in the run when the index is smaller. If
    recall declines with index size on its own, that gap is confounded and
    Q2 cannot be read as a failure effect. Run this on the BASELINE (no
    chaos) to find out.
    """
    print("\n" + "=" * 72)
    print("Q0  Is recall stable over the run? (confound check for Q2)")
    print("=" * 72)

    pts = []
    for r in rows:
        if r["reachable"] != "1":
            continue
        t, v, n = _f(r, "t_rel"), _f(r, "e2e_recall"), _f(r, "n_local")
        if not (np.isnan(t) or np.isnan(v)):
            pts.append((t, v, n))

    if len(pts) < 6:
        print("  too few samples")
        return

    pts.sort(key=lambda p: p[0])
    third = max(1, len(pts) // 3)
    first, last = pts[:third], pts[-third:]

    t = np.asarray([p[0] for p in pts])
    v = np.asarray([p[1] for p in pts])
    slope = float(np.polyfit(t, v, 1)[0]) if t.std() > 1e-9 else float("nan")

    f_mean = float(np.mean([p[1] for p in first]))
    l_mean = float(np.mean([p[1] for p in last]))
    f_n = float(np.mean([p[2] for p in first if not np.isnan(p[2])] or [float("nan")]))
    l_n = float(np.mean([p[2] for p in last if not np.isnan(p[2])] or [float("nan")]))

    print(f"  first third : e2e_recall {fmt(f_mean)}   mean n_local {f_n:,.0f}")
    print(f"  last  third : e2e_recall {fmt(l_mean)}   mean n_local {l_n:,.0f}")
    print(f"  drift       : {fmt(l_mean - f_mean, 4)} over the run "
          f"({fmt(slope * 100, 5)} per 100s)")
    print()
    if abs(l_mean - f_mean) < 0.02:
        print("  Recall is stable with index growth. Q2's before/after gap can")
        print("  be attributed to failure.")
    else:
        print("  WARNING: recall drifts with index size on its own. Q2's")
        print("  'before any kill' bucket is early-run and therefore smaller-")
        print("  index; its gap to later windows is CONFOUNDED. Compare Q2")
        print("  against this drift before claiming a failure effect.")


def q4_can_we_spot_the_bad_replica(rows) -> None:
    """The detection question, asked properly.

    The previous version correlated shard-level agreement against shard-level
    recall. That reads ~1.0 on a healthy cluster too, because when replicas
    miss the same hard queries their mutual overlap collapses onto recall
    whether or not anything is wrong -- it measured graph quality twice, not
    detection.

    The operational question is: using ONLY cross-replica comparison, can you
    identify which replica to distrust? Scored against chance.
    """
    print("\n" + "=" * 72)
    print("Q4  Can cross-replica comparison identify the degraded replica?")
    print("=" * 72)

    groups: dict[tuple[str, str], list[tuple[str, float, float]]] = {}
    for r in rows:
        if r["reachable"] != "1":
            continue
        loo, e2e = _f(r, "loo_agreement"), _f(r, "e2e_recall")
        if np.isnan(loo) or np.isnan(e2e):
            continue
        groups.setdefault((r["t_rel"], r["shard"]), []).append(
            (r["name"], loo, e2e))

    usable = [g for g in groups.values() if len(g) >= 3]
    if len(usable) < 5:
        print(f"  only {len(usable)} groups with >=3 scored replicas.")
        print("  (loo_agreement needs 3+ reachable replicas in a shard; with")
        print("   2 it is identical for both by construction.)")
        print("  If this run predates the loo_agreement column, re-run.")
        return

    hits = 0
    spearmans = []
    margins = []
    for g in usable:
        loo_min = min(g, key=lambda x: x[1])[0]
        e2e_min = min(g, key=lambda x: x[2])[0]
        if loo_min == e2e_min:
            hits += 1

        loo_v = np.asarray([x[1] for x in g])
        e2e_v = np.asarray([x[2] for x in g])
        if loo_v.std() > 1e-9 and e2e_v.std() > 1e-9:
            lr = np.argsort(np.argsort(loo_v))
            er = np.argsort(np.argsort(e2e_v))
            spearmans.append(float(np.corrcoef(lr, er)[0, 1]))

        srt = sorted(x[2] for x in g)
        margins.append(srt[1] - srt[0])          # gap between worst and next

    n_rep = float(np.mean([len(g) for g in usable]))
    chance = 1.0 / n_rep
    rate = hits / len(usable)

    print(f"  groups scored           : {len(usable)}")
    print(f"  mean replicas per group : {n_rep:.1f}")
    print(f"  worst-replica hit rate  : {fmt(rate)}   (chance {fmt(chance)})")
    print(f"  lift over chance        : {fmt(rate / chance, 2)}x")
    if spearmans:
        print(f"  within-group rank corr  : {fmt(float(np.mean(spearmans)))}")
    print(f"  mean true recall margin : {fmt(float(np.mean(margins)))}"
          "   (worst vs next-worst)")
    print()

    if rate >= chance * 1.8 and rate >= 0.55:
        print("  DETECTS: agreement identifies the degraded replica well above")
        print("  chance. That is a usable production signal -- the Layer 3")
        print("  result, and it is not available from any existing tool.")
    elif rate >= chance * 1.3:
        print("  PARTIAL: better than chance but not reliable alone. Check")
        print("  whether it sharpens with more queries per sample.")
    else:
        print("  DOES NOT DETECT: cross-replica agreement cannot single out")
        print("  the bad replica. A real result -- it means detection needs")
        print("  sentinel queries with known answers, not peer comparison.")
    print()
    print("  Compare this against the BASELINE run. A hit rate that is just")
    print("  as high with no faults is measuring noise, not detection.")


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

    q0_drift(rows)
    q1_intra_shard_spread(rows)
    q2_recall_around_failover(rows, events)
    q3_graph_vs_data(rows)
    q4_can_we_spot_the_bad_replica(rows)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
