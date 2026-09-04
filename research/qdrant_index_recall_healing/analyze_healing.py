#!/usr/bin/env python3
"""
Analysis for the index_recall healing measurement (issue #35). No cluster.

Reads results/seed<N>_{baseline,quiesce}/ and computes SPEC.md's metrics:

  per seed
    baseline range   min..max of the per-round worst-replica index_recall
                     (conditioned on that replica >= BAR indexed, as #31),
                     over the whole post-gate baseline window
    last60_mean      quiesce run: the same statistic over the LAST 60s of
                     the quiesce window                       -- primary
    healed           last60_mean >= that seed's baseline minimum
    t_to_baseline    first 30s bin after the LAST kill whose mean is >= the
                     baseline minimum and every later bin stays there (s);
                     None if never
    bins             the 30s-bin trajectory after the last kill
    killed_frac      killed node's indexed fraction per bin (alternative (ii))
    last60_compl     worst-replica completeness over the last 60s (a
                     completeness lag can raise index_recall spuriously)

Reuses analyze_gated.py's telemetry join. Written before any run.

Usage:
    python research/qdrant_index_recall_healing/analyze_healing.py [results_dir] [--bar 0.95]
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "qdrant_gated_index_recall"))
from analyze_gated import load_telemetry, fraction_at  # noqa: E402

RUN_RE = re.compile(r"^seed(\d+)_(baseline|quiesce)$")
BIN_S = 30.0
LAST_S = 60.0


def rounds_worst(samples_path, tele, bar, lo, hi):
    """[(t_rel, worst conditioned index_recall, worst completeness)] per round in [lo, hi)."""
    rounds = {}
    for r in csv.DictReader(open(samples_path)):
        try:
            t = float(r["t_rel"])
            if not (lo <= t < hi) or r.get("reachable") not in ("1", "True", "true"):
                continue
            ir = float(r["index_recall"]); comp = float(r["completeness"])
        except (KeyError, ValueError):
            continue
        node = int(r["replica"])
        f = fraction_at(tele.get(node), t)
        rounds.setdefault(t, []).append((ir, comp, f))
    out, unc = [], []
    for t, reps in sorted(rounds.items()):
        unc.append((t, min(ir for ir, _, _ in reps)))
        ok = [ir for ir, _, f in reps if f is not None and f >= bar]
        if ok:
            out.append((t, min(ok), min(c for _, c, _ in reps)))
    return out, unc


def analyze(results_dir, bar):
    per = {}
    for d in sorted(os.listdir(results_dir)):
        m = RUN_RE.match(d); p = os.path.join(results_dir, d)
        if not m or not os.path.exists(os.path.join(p, "run_meta.json")):
            continue
        seed, cond = int(m.group(1)), m.group(2)
        meta = json.load(open(os.path.join(p, "run_meta.json")))
        tele = load_telemetry(os.path.join(p, "telemetry.csv"))
        g = meta.get("index_gate") or {}
        t0 = float(g.get("gate_closed_rel") or meta.get("warmup_s") or 0)
        entry = per.setdefault(seed, {})
        if cond == "baseline":
            rw, unc = rounds_worst(os.path.join(p, "samples.csv"), tele, bar, t0, float("inf"))
            vals = [v for _, v, _ in rw]
            entry["baseline"] = {"n": len(vals), "n_all": len(unc), "min": min(vals) if vals else None,
                                 "unc_min": min(v for _, v in unc) if unc else None,
                                 "max": max(vals) if vals else None,
                                 "mean": statistics.mean(vals) if vals else None,
                                 "gate_closed": g.get("closed")}
        else:
            ev = json.load(open(os.path.join(p, "events.json")))
            kills = sorted((float(e["t_rel"]), int(str(e["target"])[-1])) for e in ev)
            t_last = kills[-1][0] if kills else float(meta.get("chaos_stop_rel") or t0)
            t_end = float(meta.get("duration_s", 0)) + t0 + 1e9  # to the last sample
            rw, unc = rounds_worst(os.path.join(p, "samples.csv"), tele, bar, t_last, float("inf"))
            if not kills:
                # Randomized chaos fired nothing in this window: there is no
                # loss to heal and the seed is UNMEASURED (SPEC alternative
                # (iii)), not healed.
                entry["quiesce"] = {"n_post": len(rw), "kills": [], "unmeasured": "no kills in the chaos window"}
                continue
            if not rw:
                entry["quiesce"] = {"n_post": 0, "kills": kills}
                continue
            t_max = max(t for t, _, _ in rw)
            last = [(v, c) for t, v, c in rw if t >= t_max - LAST_S]
            unc_last = [v for t, v in unc if t >= t_max - LAST_S]
            bin0 = [v for t, v, _ in rw if t - t_last < BIN_S]
            bins = {}
            for t, v, c in rw:
                bins.setdefault(int((t - t_last) // BIN_S), []).append(v)
            bin_means = [(k * BIN_S, statistics.mean(vs), len(vs)) for k, vs in sorted(bins.items())]
            # killed node's indexed fraction per bin
            killed = sorted({n for _, n in kills})
            kf = {}
            for n in killed:
                for t, f in tele.get(n, []):
                    if t >= t_last:
                        kf.setdefault(int((t - t_last) // BIN_S), []).append(f)
            entry["quiesce"] = {
                "kills": kills, "t_last_kill": round(t_last, 1), "n_post": len(rw),
                "last60_mean": statistics.mean(v for v, _ in last),
                "last60_min": min(v for v, _ in last),
                "last60_compl": min(c for _, c in last),
                "n_post_all": len(unc), "retained": len(rw) / len(unc) if unc else None,
                "unc_last60_mean": statistics.mean(unc_last) if unc_last else None,
                "bin0_mean": statistics.mean(bin0) if bin0 else None,
                "bins": bin_means,
                "killed_frac_bins": [(k * BIN_S, round(min(v), 3)) for k, v in sorted(kf.items())],
                "gate_closed": g.get("closed"),
            }
    return per


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("results_dir", nargs="?", default=os.path.join(HERE, "results"))
    ap.add_argument("--bar", type=float, default=0.95)
    a = ap.parse_args()
    per = analyze(a.results_dir, a.bar)
    if not per:
        print("no runs"); return 1
    print(f"bar = {a.bar}; worst replica per round, conditioned; bins of {BIN_S:.0f}s after the LAST kill; primary = last {LAST_S:.0f}s")
    print("seed        base n  base min  base max | post n  last60 mean  last60 min  healed  t_to_base  last60 compl")
    healed = 0; judged = 0
    for seed, e in sorted(per.items()):
        b, q = e.get("baseline"), e.get("quiesce")
        if q and q.get("unmeasured"):
            print(f"{seed}  UNMEASURED: {q['unmeasured']} (post rounds {q['n_post']})")
            continue
        if not b or not q or not q.get("n_post"):
            print(f"{seed}  incomplete: baseline={'yes' if b else 'no'} quiesce_post_n={q.get('n_post') if q else 'no run'}")
            continue
        ok = q["last60_mean"] >= b["min"]
        judged += 1; healed += ok
        t_to = None
        bm = q["bins"]
        for i, (t, mval, _) in enumerate(bm):
            if mval >= b["min"] and all(m2 >= b["min"] for _, m2, _ in bm[i:]):
                t_to = t; break
        print(f"{seed}  {b['n']:>6}  {b['min']:.4f}    {b['max']:.4f}   | {q['n_post']:>6}  {q['last60_mean']:.4f}       {q['last60_min']:.4f}      {'yes' if ok else 'NO ':<4}   {('never' if t_to is None else str(int(t_to))):>6}      {q['last60_compl']:.4f}")
        loss = (q["bin0_mean"] is not None and q["bin0_mean"] < b["min"])
        print(f"            post-window retention {q['retained']:.2f} ({q['n_post']}/{q['n_post_all']} rounds); unconditioned last60 mean {q['unc_last60_mean']:.4f}; "
              f"bin0 mean {q['bin0_mean']:.4f} -> {'LOSS visible after last kill' if loss else 'no loss visible after last kill'}")
        print("            bins: " + "  ".join(f"{int(t)}s:{m:.3f}(n{n})" for t, m, n in bm))
        print("            killed-node indexed frac by bin: " + "  ".join(f"{int(t)}s:{f}" for t, f in q["killed_frac_bins"]))
        print(f"            kills: {[(round(t,1), n) for t, n in q['kills']]}")
    print()
    if judged:
        unm = sum(1 for e in per.values() if e.get("quiesce", {}).get("unmeasured"))
        print(f"healed on this horizon (last60 mean >= own baseline min): {healed}/{judged} judged; {unm} seed(s) unmeasured (no kills)"
              + ("  -> outcome (a)/(b) territory" if healed >= 4 else ("  -> outcome (c): persistent in >=2/5" if judged - healed >= 2 else "")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
