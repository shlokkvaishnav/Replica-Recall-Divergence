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

SIZE_BIN = 2500


def _recall_by_size(rows) -> dict[int, tuple[float, float, int]]:
    """Bin (e2e_recall, index_recall) by index size. Fixed-width bins so two
    runs land on the same edges and can be compared directly."""
    buckets: dict[int, list[tuple[float, float]]] = {}
    for r in rows:
        if r["reachable"] != "1":
            continue
        n, e, i = _f(r, "n_local"), _f(r, "e2e_recall"), _f(r, "index_recall")
        if np.isnan(n) or np.isnan(e):
            continue
        buckets.setdefault(int(n // SIZE_BIN), []).append((e, i))
    out = {}
    for b, vals in buckets.items():
        es = [v[0] for v in vals]
        idx = [v[1] for v in vals if not np.isnan(v[1])]
        out[b] = (float(np.mean(es)),
                  float(np.mean(idx)) if idx else float("nan"),
                  len(vals))
    return out


def qsize_recall_vs_index_size(rows, compare_rows=None,
                               label="this run", compare_label="compare") -> None:
    """Recall as a function of index size -- the curve everything else needs.

    Every cross-run comparison in this experiment (Q1's spread, Q4's hit rate)
    is confounded by index size, because recall depends strongly on it and
    runs reach different sizes. Binning by n_local puts two runs on the same
    x-axis so they can be compared where they actually overlap.
    """
    print("\n" + "=" * 72)
    print("QS  Recall vs index size" + (f"   [{label} vs {compare_label}]"
                                        if compare_rows else ""))
    print("=" * 72)

    a = _recall_by_size(rows)
    b = _recall_by_size(compare_rows) if compare_rows is not None else None

    if not a:
        print("  no scored samples")
        return

    keys = sorted(set(a) | (set(b) if b else set()))

    if b is None:
        print(f"  {'index size':>16} {'e2e':>8} {'index':>8} {'n':>6}")
        print("  " + "-" * 42)
        for kbin in keys:
            if kbin not in a:
                continue
            e, i, n = a[kbin]
            lo, hi = kbin * SIZE_BIN, (kbin + 1) * SIZE_BIN
            print(f"  {lo:>7,}-{hi:<8,} {fmt(e):>8} {fmt(i):>8} {n:>6}")
        print()
        print("  A falling column is the index degrading with N, not with")
        print("  faults. Flat is what a correct HNSW should look like.")
        return

    print(f"  {'index size':>16} {label[:10]:>11} {compare_label[:10]:>11} "
          f"{'delta':>9}")
    print("  " + "-" * 52)
    overlap = []
    for kbin in keys:
        lo, hi = kbin * SIZE_BIN, (kbin + 1) * SIZE_BIN
        ea = a.get(kbin, (float("nan"),) * 3)[0]
        eb = b.get(kbin, (float("nan"),) * 3)[0]
        d = ea - eb if not (np.isnan(ea) or np.isnan(eb)) else float("nan")
        if not np.isnan(d):
            overlap.append(d)
        print(f"  {lo:>7,}-{hi:<8,} {fmt(ea):>11} {fmt(eb):>11} {fmt(d):>9}")

    print()
    if overlap:
        print(f"  overlapping bins: {len(overlap)}   "
              f"mean delta {fmt(float(np.mean(overlap)))}")
        print()
        print("  This is the only size-controlled comparison available. If the")
        print("  delta is ~0, the two runs differ only in how far they got,")
        print("  and any Q1/Q4 difference between them is a size artefact.")
    else:
        print("  No overlapping size bins -- the runs never reached comparable")
        print("  index sizes, so they cannot be compared. Match --duration and")
        print("  --writers, or run longer.")


def _intra_shard_spreads(rows) -> tuple[list[float], list[float], list[float]]:
    """Per-instant within-shard spread of e2e_recall. Shared by Q1 and the
    cross-seed aggregator so the two can never drift apart."""
    by_ts: dict[tuple[str, str], list[float]] = {}
    for r in rows:
        if r["reachable"] != "1":
            continue
        v = _f(r, "e2e_recall")
        if np.isnan(v):
            continue
        by_ts.setdefault((r["t_rel"], r["shard"]), []).append(v)

    spreads, mins, maxes = [], [], []
    for vals in by_ts.values():
        if len(vals) < 2:
            continue
        spreads.append(max(vals) - min(vals))
        mins.append(min(vals))
        maxes.append(max(vals))
    return spreads, mins, maxes


def resolution_eps(meta) -> float:
    """Smallest recall difference this measurement can represent.

    Mean recall@k over nq queries moves in steps of 1/(k*nq): one query
    gaining or losing one correct neighbour. A gap below that is not a small
    difference, it is no difference -- the replicas returned identical result
    sets and the arithmetic is showing float noise. Half a step is used as
    the tie threshold.

    Derived from the run's own parameters rather than chosen, so it cannot be
    tuned to make a result appear.
    """
    try:
        k = int(meta.get("k") or 10)
        nq = int(meta.get("queries") or 100)
        return 0.5 / float(k * nq)
    except Exception:
        return 0.0005


def _detection_stats(rows, tie_eps: float = 0.0) -> dict | None:
    """Q4's numbers as data rather than print output.

    Groups where the worst and second-worst replica are within `tie_eps` are
    excluded, not scored. When every replica performs identically there is no
    degraded replica to find, so "did the detector pick the worst one" has no
    correct answer -- and min() over tied values silently returns whichever
    comes first in list order, identically for both the detector and the
    truth, manufacturing a hit.

    That artifact is not hypothetical: on realistic data the healthy baseline
    scored 0.6409 against a chance of 0.333, purely from ties, with a rank
    correlation standard deviation of 0.86.
    """
    groups: dict[tuple[str, str], list[tuple[str, float, float]]] = {}
    for r in rows:
        if r["reachable"] != "1":
            continue
        loo, e2e = _f(r, "loo_agreement"), _f(r, "e2e_recall")
        if np.isnan(loo) or np.isnan(e2e):
            continue
        groups.setdefault((r["t_rel"], r["shard"]), []).append(
            (r["name"], loo, e2e))

    candidates = [g for g in groups.values() if len(g) >= 3]

    scored, tied = [], 0
    for g in candidates:
        srt = sorted(x[2] for x in g)
        if (srt[1] - srt[0]) <= tie_eps:
            tied += 1
            continue
        scored.append(g)

    if len(scored) < 5:
        return {
            "groups": len(scored),
            "tied_excluded": tied,
            "candidates": len(candidates),
            "n_replicas": float("nan"),
            "hit_rate": float("nan"),
            "chance": float("nan"),
            "rank_corr": float("nan"),
            "margin": float("nan"),
        } if candidates else None

    hits, spearmans, margins = 0, [], []
    for g in scored:
        if min(g, key=lambda x: x[1])[0] == min(g, key=lambda x: x[2])[0]:
            hits += 1
        loo_v = np.asarray([x[1] for x in g])
        e2e_v = np.asarray([x[2] for x in g])
        if loo_v.std() > 1e-9 and e2e_v.std() > 1e-9:
            spearmans.append(float(np.corrcoef(
                np.argsort(np.argsort(loo_v)), np.argsort(np.argsort(e2e_v)))[0, 1]))
        srt = sorted(x[2] for x in g)
        margins.append(srt[1] - srt[0])

    n_rep = float(np.mean([len(g) for g in scored]))
    return {
        "groups": len(scored),
        "tied_excluded": tied,
        "candidates": len(candidates),
        "n_replicas": n_rep,
        "hit_rate": hits / len(scored),
        "chance": 1.0 / n_rep,
        "rank_corr": float(np.mean(spearmans)) if spearmans else float("nan"),
        "margin": float(np.mean(margins)),
    }


def summarize_run(rows, meta=None) -> dict:
    """One row of numbers per experiment run, for cross-seed aggregation."""
    spreads, _, _ = _intra_shard_spreads(rows)
    det = _detection_stats(rows, resolution_eps(meta or {})) or {}

    idx, comp, e2e = [], [], []
    for r in rows:
        if r["reachable"] != "1":
            continue
        i, c, e = (_f(r, "index_recall"), _f(r, "completeness"),
                   _f(r, "e2e_recall"))
        if not np.isnan(i):
            idx.append(i)
        if not np.isnan(c):
            comp.append(c)
        if not np.isnan(e):
            e2e.append(e)

    total = len(rows)
    unreach = sum(1 for r in rows if r["reachable"] != "1")
    return {
        "spread_mean": float(np.mean(spreads)) if spreads else float("nan"),
        "spread_p95": (float(np.percentile(spreads, 95))
                       if spreads else float("nan")),
        "index_recall": nanmean(idx),
        "completeness": nanmean(comp),
        "e2e_recall": nanmean(e2e),
        "hit_rate": det.get("hit_rate", float("nan")),
        "rank_corr": det.get("rank_corr", float("nan")),
        "margin": det.get("margin", float("nan")),
        "chance": det.get("chance", float("nan")),
        "tied_excluded": det.get("tied_excluded", 0),
        "detector_groups": det.get("groups", 0),
        "unreachable_frac": unreach / total if total else float("nan"),
        "by_size": _recall_by_size(rows),
    }


def q1_intra_shard_spread(rows) -> None:
    print("\n" + "=" * 72)
    print("Q1  Do replicas of the same shard disagree at the same instant?")
    print("=" * 72)

    spreads, mins, maxes = _intra_shard_spreads(rows)

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


def heal_stats(rows, meta) -> dict | None:
    """Reduce a quiesce run to before / during / after-chaos means.

    Returns None for runs without a quiesce window.
    """
    if not meta or not meta.get("quiesce"):
        return None
    t0, t1 = meta.get("chaos_start_rel"), meta.get("chaos_stop_rel")
    if t0 is None or t1 is None:
        return None

    phases = {"pre": [], "during": [], "post0_30": [],
              "post30_60": [], "post60p": []}
    for r in rows:
        if r["reachable"] != "1":
            continue
        t = _f(r, "t_rel")
        c, e, ni = (_f(r, "completeness"), _f(r, "e2e_recall"),
                    _f(r, "n_intended"))
        if np.isnan(t) or np.isnan(c):
            continue
        # The ABSOLUTE number of intended ids this replica is missing.
        #
        # completeness alone cannot answer the healing question: it is a
        # ratio, writes keep flowing during the quiesce window, and a growing
        # denominator drags it toward 1.0 even when not one missed write has
        # been recovered. Measured here, completeness climbed 0.9607 -> 0.9694
        # after faults stopped while the missing count went 590 -> 600. That
        # reads as partial recovery and is pure dilution.
        #
        # n_intended - n_local will NOT do instead: n_local includes writes
        # too recent to have settled, so that difference is negative on a
        # healthy replica.
        missing = (1.0 - c) * ni if not np.isnan(ni) else float("nan")
        if t < t0:
            k = "pre"
        elif t < t1:
            k = "during"
        else:
            dt = t - t1
            k = ("post0_30" if dt < 30 else
                 "post30_60" if dt < 60 else "post60p")
        phases[k].append((c, e, missing))

    out = {}
    for k, vals in phases.items():
        out[k] = {
            "completeness": nanmean([v[0] for v in vals]),
            "e2e_recall": nanmean([v[1] for v in vals]),
            "missing": nanmean([v[2] for v in vals]),
            "n": len(vals),
        }

    pre_m = out["pre"]["missing"]
    last = next((out[k] for k in ("post60p", "post30_60", "post0_30")
                 if out[k]["n"] > 0), None)
    # Damage is measured at the moment faults stop, not averaged over the
    # chaos window (which understates it -- early chaos has done less).
    at_stop = out["post0_30"] if out["post0_30"]["n"] > 0 else last

    if last is None or at_stop is None or np.isnan(pre_m):
        out["healed"] = None
        out["recovered_frac"] = float("nan")
        out["residual_missing"] = float("nan")
    else:
        damage = at_stop["missing"] - pre_m
        residual = last["missing"] - pre_m
        out["residual_missing"] = residual
        out["recovered_frac"] = (
            1.0 - residual / damage if damage > 1e-9 else float("nan"))
        # Healed means the writes actually came back, so judge on the count.
        out["healed"] = (None if damage <= 1e-9
                         else bool(out["recovered_frac"] >= 0.9))

    return out


def qheal_recovery(rows, meta) -> None:
    """Does the cluster heal once the failures stop?

    Runs with faults throughout measure a steady state: ongoing damage
    balanced against whatever repair exists. That cannot distinguish "damage
    is being repaired as fast as it accrues" from "damage accumulates and
    nothing repairs it". Stopping the faults and continuing to watch does.

    Completeness is the right metric here -- unlike recall it does not drift
    with index size, and a healthy cluster holds it at exactly 1.0000.
    """
    h = heal_stats(rows, meta)
    if h is None:
        return

    print("\n" + "=" * 72)
    print("QH  Does the cluster heal after the failures stop?")
    print("=" * 72)
    print(f"  chaos window: {meta['chaos_start_rel']:.0f}s -> "
          f"{meta['chaos_stop_rel']:.0f}s")
    print()
    print(f"  {'phase':<22} {'n':>4} {'completeness':>13} {'missing ids':>12} "
          f"{'e2e_recall':>11}")
    print("  " + "-" * 66)
    labels = [("pre", "before chaos"), ("during", "during chaos"),
              ("post0_30", "0-30s after stop"),
              ("post30_60", "30-60s after stop"),
              ("post60p", ">60s after stop")]
    for key, label in labels:
        p = h[key]
        if p["n"] == 0:
            continue
        miss = ("  n/a" if np.isnan(p["missing"]) else f"{p['missing']:.0f}")
        print(f"  {label:<22} {p['n']:>4} {fmt(p['completeness']):>13} "
              f"{miss:>12} {fmt(p['e2e_recall']):>11}")

    print()
    print("  Judge on 'missing ids', not completeness. Writes keep flowing")
    print("  during the quiesce window, so a growing denominator drags the")
    print("  ratio toward 1.0 even if nothing is ever recovered.")
    print()
    if h["healed"] is None:
        print("  Not enough damage or too few samples in a phase to judge.")
    elif h["healed"]:
        print(f"  HEALS: {h['recovered_frac']:.0%} of the writes missed during")
        print("  the outage were recovered once faults stopped. The divergence")
        print("  is transient, and the thesis weakens considerably -- a")
        print("  transient gap is far less interesting than a permanent one.")
    else:
        print(f"  DOES NOT HEAL: {h['recovered_frac']:.0%} of missed writes "
              f"recovered;")
        print(f"  {h['residual_missing']:.0f} ids still absent well after the "
              f"last fault.")
        print("  A replica that missed writes while it was down never gets")
        print("  them back: no anti-entropy, no read-repair, no catch-up.")
        print("  Every query routed there returns silently worse results,")
        print("  indefinitely.")
        print()
        print("  This is the headline result if it holds across seeds.")


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


def q4_can_we_spot_the_bad_replica(rows, meta=None) -> None:
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

    det = _detection_stats(rows, resolution_eps(meta or {}))
    if det is None:
        print("  fewer than 5 groups with >=3 scored replicas.")
        print("  (loo_agreement needs 3+ reachable replicas in a shard; with")
        print("   2 it is identical for both by construction.)")
        print("  If this run predates the loo_agreement column, re-run.")
        return

    rate, chance, n_rep = det["hit_rate"], det["chance"], det["n_replicas"]

    if np.isnan(det.get("hit_rate", float("nan"))):
        print(f"  {det.get('candidates', 0)} candidate groups, "
              f"{det.get('tied_excluded', 0)} excluded as ties, "
              f"{det.get('groups', 0)} scorable.")
        print("  Too few groups where any replica is actually distinguishable")
        print("  to say anything about detection.")
        return
    print(f"  groups scored           : {det['groups']}"
          f"   ({det.get('tied_excluded', 0)} excluded as ties of "
          f"{det.get('candidates', 0)} candidates)")
    print(f"  mean replicas per group : {n_rep:.1f}")
    print(f"  worst-replica hit rate  : {fmt(rate)}   (chance {fmt(chance)})")
    print(f"  lift over chance        : {fmt(rate / chance, 2)}x")
    print(f"  within-group rank corr  : {fmt(det['rank_corr'])}")
    print(f"  mean true recall margin : {fmt(det['margin'])}"
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
    ap.add_argument("--compare-dir", default=None,
                    help="a second results dir to overlay in QS, binned by "
                         "index size (the only size-controlled comparison)")
    args = ap.parse_args()

    rows, events, meta = load(args.results_dir)
    compare_rows = load(args.compare_dir)[0] if args.compare_dir else None

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
        # The corpus distribution decides what recall numbers can mean at all
        # -- uniform 128-d suffers distance concentration and depresses recall
        # for reasons unrelated to the index. It was recorded in run_meta.json
        # and never printed, which made a uniform report and a SIFT report
        # indistinguishable on the page. That is how a number gets quoted
        # against the wrong corpus.
        print(f"  corpus={meta.get('dist', 'unknown')}  "
              f"metric={meta.get('metric')}")
        if meta.get("corpus_exhausted"):
            print("  WARNING: the corpus pool ran out and writers stopped "
                  "early; the write rate fell for reasons unrelated to the "
                  "cluster.")
    print(f"  rows={len(rows)}  chaos_events={len(events)}")

    unreachable = sum(1 for r in rows if r["reachable"] != "1")
    print(f"  unreachable samples: {unreachable}/{len(rows)} "
          f"({100.0 * unreachable / max(len(rows), 1):.1f}%)")

    qheal_recovery(rows, meta)
    q0_drift(rows)
    qsize_recall_vs_index_size(
        rows, compare_rows,
        label=os.path.basename(args.results_dir.rstrip("/\\")) or "this run",
        compare_label=(os.path.basename(args.compare_dir.rstrip("/\\"))
                       if args.compare_dir else "compare"))
    q1_intra_shard_spread(rows)
    q2_recall_around_failover(rows, events)
    q3_graph_vs_data(rows)
    q4_can_we_spot_the_bad_replica(rows, meta)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
