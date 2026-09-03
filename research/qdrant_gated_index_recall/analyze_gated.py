#!/usr/bin/env python3
"""
Analysis for the gated index_recall re-run (issue #30). No cluster needed.

Reads results/seed<N>_<condition>/ (qdrant_sweep.py's layout, so
../replica_recall/aggregate.py still works unmodified for the continuity
metrics) and computes SPEC.md's primary metric:

  per seed, per condition: mean index_recall on the WORST replica per sample
  round, over samples whose replica was >= BAR indexed at that instant
  (telemetry.csv joined on replica == node, nearest t_rel)

then the exact two-sided Mann-Whitney U at 5 vs 5 (aggregate.mann_whitney,
reused) for chaos vs baseline -- CONDITIONED (the pre-registered decision
metric) and UNCONDITIONED (what PR #6 would have seen), plus the retained
sample fraction per arm, which decides whether the conditioned comparison is
powered at all (SPEC.md Confounds: <50% retained in either arm => say so).

Also reads results/run0/gate_scores.json if present and reports the
before/after-gate index_recall per replica -- outcome (e)'s check.

Written before any run, per GIT_WORKFLOW.md.

Usage:
    python research/qdrant_gated_index_recall/analyze_gated.py [results_dir] [--bar 0.95]
"""
from __future__ import annotations

import argparse
import bisect
import csv
import json
import os
import re
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "replica_recall"))
from aggregate import mann_whitney  # noqa: E402

RUN_RE = re.compile(r"^seed(\d+)_(baseline|chaos|quiesce)$")


def load_telemetry(path):
    """node -> sorted [(t_rel, indexed_fraction)]"""
    by = {}
    for r in csv.DictReader(open(path)):
        try:
            p = float(r["points_count"] or 0)
            f = min(1.0, float(r["indexed_vectors_count"] or 0) / p) if p > 0 else 0.0
            by.setdefault(int(r["node"]), []).append((float(r["t_rel"]), f))
        except (KeyError, ValueError):
            continue
    return {n: sorted(v) for n, v in by.items()}


def fraction_at(tele_node, t):
    """indexed fraction of one node at the telemetry sample nearest t."""
    if not tele_node:
        return None
    ts = [x[0] for x in tele_node]
    i = bisect.bisect_left(ts, t)
    cands = [j for j in (i - 1, i) if 0 <= j < len(ts)]
    j = min(cands, key=lambda j: abs(ts[j] - t))
    return tele_node[j][1]


def per_round(samples_path, tele, bar, window=None):
    """Group samples by t_rel; per round take the worst reachable replica's
    index_recall, twice: over all replicas (unconditioned) and over replicas
    whose indexed fraction >= bar (conditioned). Returns
    (uncond_list, cond_list, n_rounds, n_rounds_with_any_conditioned)."""
    rounds = {}
    for r in csv.DictReader(open(samples_path)):
        try:
            t = float(r["t_rel"])
            if window and not (window[0] <= t < window[1]):
                continue
            if r.get("reachable") not in ("1", "True", "true"):
                continue
            ir = float(r["index_recall"])
        except (KeyError, ValueError):
            continue
        node = int(r["replica"])
        frac = fraction_at(tele.get(node), t)
        rounds.setdefault(t, []).append((ir, frac))
    unc, con, strict = [], [], []
    for t, reps in sorted(rounds.items()):
        unc.append(min(ir for ir, _ in reps))
        ok = [ir for ir, f in reps if f is not None and f >= bar]
        if ok:
            con.append(min(ok))
        # Secondary, stricter reading: keep the round only if EVERY replica
        # is >= bar, so the worst replica is never swapped out by the
        # condition itself. Pre-registered metric is `con`; this shows how
        # much the choice of unit matters.
        if len(ok) == len(reps):
            strict.append(min(ok))
    return unc, con, len(rounds), len(con), strict


def window_for(meta, cond):
    """Chaos runs score the chaos window; baseline scores from gate close on."""
    t0 = (meta.get("index_gate") or {}).get("gate_closed_rel") or meta.get("warmup_s") or 0
    if cond == "baseline" or meta.get("chaos_start_rel") is None:
        return (float(t0), float("inf"))
    return (float(meta["chaos_start_rel"]), float(meta.get("chaos_stop_rel") or float("inf")))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("results_dir", nargs="?", default=os.path.join(HERE, "results"))
    ap.add_argument("--bar", type=float, default=0.95)
    a = ap.parse_args()

    run0 = os.path.join(a.results_dir, "run0", "gate_scores.json")
    if os.path.exists(run0):
        g = json.load(open(run0))
        print("run 0 -- index_recall before vs after the gate, per replica:")
        for r in g.get("before_gate", []):
            aft = next((x for x in g.get("after_gate", []) if x["name"] == r["name"]), {})
            print(f"  {r['name']}: before {r.get('index_recall')}  after {aft.get('index_recall')}  "
                  f"delta {g.get('delta_index_recall', {}).get(r['name'])}")
        deltas = [v for v in g.get("delta_index_recall", {}).values() if v is not None]
        print(f"  original before/after prediction: "
              f"{'HOLDS' if deltas and max(deltas) < 0 else 'DOES NOT HOLD'} "
              f"(deltas {deltas}) -- uninformative when the gate closes at once; Amendment 1")
        if g.get("after_gate_exact"):
            print("  Amendment 1 -- HNSW vs exact on the same post-gate state:")
            ex = {r["name"]: r for r in g["after_gate_exact"]}
            for r in g.get("after_gate", []):
                print(f"    {r['name']}: hnsw {r.get('index_recall')}  "
                      f"exact {ex.get(r['name'], {}).get('index_recall')}")
            exv = [float(r["index_recall"]) for r in g["after_gate_exact"]
                   if r.get("index_recall") is not None]
            hv = [float(r["index_recall"]) for r in g.get("after_gate", [])
                  if r.get("index_recall") is not None]
            ok_exact = bool(exv) and min(exv) >= 0.9995
            ok_hnsw = bool(hv) and min(hv) < 0.9995
            print(f"    exact == 1.000 on every replica: {'YES' if ok_exact else 'NO'};  "
                  f"HNSW < 1.000 on some replica: {'YES' if ok_hnsw else 'NO'}  -> "
                  f"{'graph traversed' if ok_exact and ok_hnsw else 'NOT established'}")
        print()

    per = {}   # cond -> seed -> dict
    for d in sorted(os.listdir(a.results_dir)):
        m = RUN_RE.match(d)
        p = os.path.join(a.results_dir, d)
        if not m or not os.path.exists(os.path.join(p, "run_meta.json")):
            continue
        seed, cond = int(m.group(1)), m.group(2)
        meta = json.load(open(os.path.join(p, "run_meta.json")))
        tele = load_telemetry(os.path.join(p, "telemetry.csv")) \
            if os.path.exists(os.path.join(p, "telemetry.csv")) else {}
        unc, con, n_rounds, n_con, strict = per_round(os.path.join(p, "samples.csv"), tele, a.bar,
                                                      window_for(meta, cond))
        gate = meta.get("index_gate") or {}
        per.setdefault(cond, {})[seed] = {
            "unc_mean": statistics.mean(unc) if unc else None,
            "con_mean": statistics.mean(con) if con else None,
            "strict_mean": statistics.mean(strict) if strict else None,
            "n_strict": len(strict),
            "n_rounds": n_rounds, "n_con": n_con,
            "retained": (n_con / n_rounds) if n_rounds else None,
            "gate_closed": gate.get("closed"), "gate_s": gate.get("elapsed_s"),
            "written": meta.get("written_at_gate"),
        }

    print(f"bar = {a.bar}   (per-round worst replica; conditioned = replicas >= bar indexed)")
    print("cond      seed        gate   written   rounds  retained   uncond_mean  cond_mean  strict_mean(n)")
    for cond in ("baseline", "chaos", "quiesce"):
        for seed, r in sorted(per.get(cond, {}).items()):
            print(f"{cond:<9} {seed:<10} {str(r['gate_closed']):<6} {r['written']!s:>7}  "
                  f"{r['n_rounds']:>6}  {r['retained'] if r['retained'] is None else round(r['retained'], 2)!s:>8}  "
                  f"{'' if r['unc_mean'] is None else round(r['unc_mean'], 4)!s:>11}  "
                  f"{'' if r['con_mean'] is None else round(r['con_mean'], 4)!s:>9}  "
                  f"{'' if r['strict_mean'] is None else round(r['strict_mean'], 4)!s:>8}({r['n_strict']})")
    print()

    b, c = per.get("baseline", {}), per.get("chaos", {})
    if len(b) >= 2 and len(c) >= 2:
        for label, key in (("CONDITIONED (decision metric)", "con_mean"),
                           ("unconditioned (what #6 saw)", "unc_mean"),
                           ("strict (every replica >= bar; secondary)", "strict_mean")):
            xb = [r[key] for r in b.values() if r[key] is not None]
            xc = [r[key] for r in c.values() if r[key] is not None]
            if len(xb) >= 2 and len(xc) >= 2:
                u, pval = mann_whitney(xb, xc)
                print(f"{label}: baseline mean {statistics.mean(xb):.4f} (n={len(xb)}) vs "
                      f"chaos mean {statistics.mean(xc):.4f} (n={len(xc)})  U={u}  p={pval:.4f}")
            else:
                print(f"{label}: not enough runs with a value (baseline {len(xb)}, chaos {len(xc)})")
        rb = [r["retained"] for r in b.values() if r["retained"] is not None]
        rc = [r["retained"] for r in c.values() if r["retained"] is not None]
        if rb and rc:
            print(f"retained sample fraction: baseline {min(rb):.2f}-{max(rb):.2f}, "
                  f"chaos {min(rc):.2f}-{max(rc):.2f}"
                  + ("   ** an arm retains <50%: conditioned comparison is under-powered, say so **"
                     if min(rb + rc) < 0.5 else ""))
        base_spread = [r["con_mean"] for r in b.values() if r["con_mean"] is not None]
        if base_spread:
            print(f"baseline conditioned index_recall spread: {min(base_spread):.4f}-{max(base_spread):.4f}"
                  + ("   (no headroom: outcome (c) territory)" if min(base_spread) > 0.995 else ""))
    else:
        print("fewer than 2 runs per arm; no test")
    return 0


if __name__ == "__main__":
    sys.exit(main())
