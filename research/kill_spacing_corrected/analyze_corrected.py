#!/usr/bin/env python3
"""Compare accumulated damage across kill-spacing conditions (#24).

SPEC.md fixes the primary metric BEFORE any run: mean missing ids over the
observation window -- integrated missing-id-seconds divided by window duration,
so it cannot be inflated by one condition being watched longer. Peak missing and
spikes-per-kill are secondary, the last of those being a direct check that
Amendment 2's sampling fix worked.

Validity preconditions (Amendment 4) are evaluated and printed BEFORE any
comparison, and a failing condition is named rather than quietly dropped:

  * realized sample interval <= 4s (the transient must be resolved)
  * >= 2 damage spikes per kill on average (otherwise still under-sampled)
  * no condition showing zero damage across all seeds
  * realized short/long gaps must not overlap
  * corpus must not exhaust (no writes in flight means nothing to lose)

The statistics are aggregate.py's exact mann_whitney, not a reimplementation.

Usage:
    python research/kill_spacing_corrected/analyze_corrected.py
"""
import argparse
import csv
import glob
import itertools
import json
import os
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "replica_recall"))
from aggregate import mann_whitney  # noqa: E402

CONDITIONS = ("short-gap-same-node", "long-gap-same-node", "spread")
MAX_INTERVAL_S = 4.0
MIN_SPIKES_PER_KILL = 2.0


def load(run_dir):
    meta = json.load(open(os.path.join(run_dir, "run_meta.json")))
    events = json.load(open(os.path.join(run_dir, "events.json")))
    rows = list(csv.DictReader(open(os.path.join(run_dir, "samples.csv"))))

    # Worst-affected replica per sample round: the question is whether ANY
    # replica is behind, not the average across replicas (averaging would dilute
    # one damaged replica against two healthy ones).
    pts = {}
    for r in rows:
        if r["reachable"] != "1" or not r["completeness"] or not r["n_intended"]:
            continue
        t = round(float(r["t_rel"]), 2)
        miss = (1.0 - float(r["completeness"])) * float(r["n_intended"])
        pts[t] = max(pts.get(t, 0.0), miss)
    ts = sorted(pts)

    # A damaged SAMPLE and a damage EPISODE are different things, and conflating
    # them overstates how much better the sampling got: finer sampling counts the
    # same episode more times, which is a real gain but not the same gain as
    # observing more episodes. Amendment 4's ">= 2 spikes per kill" means
    # episodes; both are reported so the precondition cannot be read two ways.
    episodes, run = [], 0
    for t in ts:
        if pts[t] > 0.5:
            run += 1
        elif run:
            episodes.append(run)
            run = 0
    if run:
        episodes.append(run)

    t0 = meta["chaos_start_rel"]
    obs = [(t, pts[t]) for t in ts if t >= t0]      # chaos start -> run end
    integrated = sum((obs[i][0] - obs[i - 1][0]) * (obs[i][1] + obs[i - 1][1]) / 2
                     for i in range(1, len(obs)))
    dur = (obs[-1][0] - obs[0][0]) if len(obs) > 1 else 0.0

    return {
        "name": os.path.basename(run_dir),
        "condition": events[0].get("condition") if events else None,
        "seed": meta.get("seed"),
        "n_kills": len(events),
        "interval": statistics.median([ts[i] - ts[i - 1] for i in range(1, len(ts))]),
        "n_samples": len(ts),
        "n_damaged": sum(1 for t in ts if pts[t] > 0.5),   # samples
        "n_episodes": len(episodes),                       # contiguous runs
        "episode_lens": episodes,
        "mean_missing": (integrated / dur) if dur else float("nan"),   # PRIMARY
        "integrated": integrated,
        "peak": max(pts.values()) if pts else 0.0,
        "gaps": [e["realized_gap_s"] for e in events
                 if e.get("realized_gap_s") is not None],
        "exhausted": meta.get("corpus_exhausted"),
        "killed_while_down": sum(1 for e in events if e.get("killed_while_down")),
        "confirmed": meta.get("confirmed_total"),
        "wfail": meta.get("write_failed"),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--results-dir",
                    default="research/kill_spacing_corrected/results")
    args = ap.parse_args()

    runs = [load(d) for d in sorted(glob.glob(os.path.join(args.results_dir, "*")))
            if os.path.isfile(os.path.join(d, "run_meta.json"))]
    if not runs:
        print(f"no runs under {args.results_dir}")
        return 1

    print("=" * 78)
    print("Validity preconditions (SPEC.md Amendment 4) -- evaluated before comparing")
    print("=" * 78)
    print(f"{'run':<36}{'interval':>9}{'samples':>8}{'dmgd':>6}"
          f"{'episodes':>9}{'eps/kill':>9}{'exhaust':>9}{'kwd':>5}")
    fails = []
    for r in sorted(runs, key=lambda x: (x["condition"] or "", x["seed"] or 0)):
        epk = r["n_episodes"] / r["n_kills"] if r["n_kills"] else 0
        print(f"{r['name']:<36}{r['interval']:>9.2f}{r['n_samples']:>8}"
              f"{r['n_damaged']:>6}{r['n_episodes']:>9}{epk:>9.2f}"
              f"{str(r['exhausted']):>9}{r['killed_while_down']:>5}")
        if r["interval"] > MAX_INTERVAL_S:
            fails.append(f"{r['name']}: interval {r['interval']:.2f}s > {MAX_INTERVAL_S}s")
        if r["exhausted"]:
            fails.append(f"{r['name']}: corpus exhausted")
        if r["killed_while_down"]:
            fails.append(f"{r['name']}: {r['killed_while_down']} kill(s) on a down node")

    kills = sum(r["n_kills"] for r in runs)
    all_eps = sum(r["n_episodes"] for r in runs) / kills
    all_smp = sum(r["n_damaged"] for r in runs) / kills
    all_len = [x for r in runs for x in r["episode_lens"]]
    print(f"\n  EPISODES per kill        : {all_eps:.2f}  "
          f"(precondition >= {MIN_SPIKES_PER_KILL}) -> "
          f"{'PASS' if all_eps >= MIN_SPIKES_PER_KILL else 'FAIL'}")
    print(f"  damaged SAMPLES per kill : {all_smp:.2f}  "
          f"(NOT the precondition -- finer sampling inflates this by counting "
          f"one episode repeatedly)")
    if all_len:
        print(f"  samples per episode      : mean {statistics.mean(all_len):.2f}"
              f"  max {max(all_len)}  "
              f"single-sample {sum(1 for x in all_len if x == 1)}/{len(all_len)}")
    print(f"  median interval         : "
          f"{statistics.median([r['interval'] for r in runs]):.2f}s  "
          f"(precondition <= {MAX_INTERVAL_S}s)")
    if all_eps < MIN_SPIKES_PER_KILL:
        fails.append(f"episodes per kill {all_eps:.2f} < {MIN_SPIKES_PER_KILL} "
                     f"(damaged samples per kill is {all_smp:.2f}, but that is "
                     f"not what the precondition asks)")

    print("\n  realized gaps (Amendment 1 -- realized, never requested):")
    spans = {}
    for c in CONDITIONS:
        g = [x for r in runs if r["condition"] == c for x in r["gaps"]]
        spans[c] = (min(g), max(g)) if g else None
        print(f"    {c:<22} n={len(g):>3}  " +
              (f"{min(g):.2f}-{max(g):.2f}s  median {statistics.median(g):.2f}s"
               if g else "none by construction -- each node killed once"))
    s, l = spans["short-gap-same-node"], spans["long-gap-same-node"]
    if s and l:
        overlap = not (s[1] < l[0] or l[1] < s[0])
        print(f"    short/long overlap: "
              f"{'YES -- CONTAMINATED' if overlap else 'no -- cleanly separated'}")
        if overlap:
            fails.append("short and long conditions overlap in realized spacing")

    by = {c: [r for r in runs if r["condition"] == c] for c in CONDITIONS}
    for c, rs in by.items():
        if rs and all(r["n_damaged"] == 0 for r in rs):
            fails.append(f"{c}: zero damage across all seeds -- void, not 'healed'")

    print("\n" + ("  PRECONDITIONS PASS" if not fails else "  PRECONDITION FAILURES:"))
    for f in fails:
        print(f"    - {f}")

    print("\n" + "=" * 78)
    print("PRIMARY: mean missing ids over the observation window "
          "(duration-normalized)")
    print("=" * 78)
    print(f"{'condition':<22}{'n':>3}{'mean':>10}{'median':>10}"
          f"{'per-seed':>34}")
    for c in CONDITIONS:
        v = sorted(r["mean_missing"] for r in by[c])
        if v:
            print(f"{c:<22}{len(v):>3}{statistics.mean(v):>10.1f}"
                  f"{statistics.median(v):>10.1f}"
                  f"{str([round(x, 1) for x in v]):>34}")

    for label, key in (("PRIMARY mean missing", "mean_missing"),
                       ("secondary: peak missing", "peak"),
                       ("secondary: integrated missing-seconds", "integrated")):
        print(f"\n  {label}")
        for a, b in itertools.combinations(CONDITIONS, 2):
            va = [r[key] for r in by[a]]
            vb = [r[key] for r in by[b]]
            if len(va) < 2 or len(vb) < 2:
                print(f"    {a} vs {b}: too few runs")
                continue
            u, p = mann_whitney(va, vb)
            print(f"    {a:<22} {statistics.mean(va):>10.1f}  vs  {b:<22}"
                  f"{statistics.mean(vb):>10.1f}   U={u:<6.1f} p={p:.4f}"
                  f"{'*' if p < 0.05 else ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
