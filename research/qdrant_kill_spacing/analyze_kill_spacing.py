#!/usr/bin/env python3
"""Compare post-chaos healing across controlled kill-spacing conditions (#9).

Reads the sweep this branch's SPEC.md pre-registers: three conditions
(short-gap-same-node, long-gap-same-node, spread) x five paired seeds, run with
`--kill-schedule` from `research/qdrant_kill_scheduler/`.

Nothing statistical or metric-shaped is reimplemented here. Healing comes from
`replica_recall/analyze.py`'s `heal_stats` -- the same function the nano-db and
Qdrant results already use, including its absolute-missing-count treatment of
the dilution trap -- and the test is `aggregate.py`'s exact `mann_whitney`. This
script's own job is only to group runs by condition, enforce the spec's validity
preconditions, and print the comparison.

Validity preconditions are checked and reported BEFORE any comparison, because
SPEC.md's Amendment 4 pre-registers what makes a run or a condition
uninterpretable. A void run is named, not silently dropped.

Usage:
    python research/qdrant_kill_spacing/analyze_kill_spacing.py \
        [--results-dir research/qdrant_kill_spacing/results]
"""
import argparse
import csv
import glob
import json
import os
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "replica_recall"))

import numpy as np  # noqa: E402
from analyze import heal_stats  # noqa: E402
from aggregate import mann_whitney  # noqa: E402

CONDITIONS = ("short-gap-same-node", "long-gap-same-node", "spread")


def load_run(run_dir):
    meta = json.load(open(os.path.join(run_dir, "run_meta.json")))
    events = json.load(open(os.path.join(run_dir, "events.json")))
    rows = list(csv.DictReader(open(os.path.join(run_dir, "samples.csv"))))
    h = heal_stats(rows, meta)
    gaps = [e["realized_gap_s"] for e in events
            if e.get("realized_gap_s") is not None]
    attempted = meta.get("write_attempted") or 0
    failed = meta.get("write_failed") or 0
    return {
        "dir": run_dir,
        "name": os.path.basename(run_dir),
        "condition": (events[0].get("condition") if events else None),
        "seed": meta.get("seed"),
        "heal": h,
        "realized_gaps": gaps,
        "n_kills": len(events),
        "killed_while_down": sum(1 for e in events if e.get("killed_while_down")),
        "corpus_exhausted": meta.get("corpus_exhausted"),
        "chaos_start": meta.get("chaos_start_rel"),
        "write_failed": failed,
        "write_attempted": attempted,
        "write_fail_rate": (failed / attempted) if attempted else float("nan"),
        "confirmed": meta.get("confirmed_total"),
    }


def fmt(x, nd=1):
    if x is None:
        return "--"
    try:
        if np.isnan(x):
            return "nan"
    except TypeError:
        return str(x)
    return f"{x:.{nd}f}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--results-dir",
                    default="research/qdrant_kill_spacing/results")
    args = ap.parse_args()

    runs = [load_run(d) for d in sorted(glob.glob(os.path.join(args.results_dir, "*")))
            if os.path.isfile(os.path.join(d, "run_meta.json"))]
    if not runs:
        print(f"no runs under {args.results_dir}")
        return 1

    # ---------------------------------------------------------------- validity
    print("=" * 74)
    print("Validity preconditions (SPEC.md Amendment 2 and 4)")
    print("=" * 74)
    void, warn = [], []
    for r in runs:
        # Amendment 2: a run whose corpus ran out before/inside chaos has no
        # writes in flight to lose, so its healing metric is meaningless.
        if r["corpus_exhausted"]:
            void.append(r["name"])
        if r["killed_while_down"]:
            warn.append(f"{r['name']}: {r['killed_while_down']} kill(s) landed "
                        f"on a container that had not restarted")
    print(f"  runs found                    : {len(runs)}")
    print(f"  void (corpus exhausted)       : {len(void)}"
          + (f" -> {', '.join(void)}" if void else ""))
    print(f"  killed_while_down occurrences : {len(warn)}")
    for w in warn:
        print(f"      {w}")

    live = [r for r in runs if r["name"] not in void]

    # Amendment 4: the conditions must stay separated in REALIZED terms.
    print("\n  realized gaps by condition (Amendment 1 -- realized, not requested):")
    spans = {}
    for c in CONDITIONS:
        g = [x for r in live if r["condition"] == c for x in r["realized_gaps"]]
        spans[c] = (min(g), max(g)) if g else None
        # Distinguish the three reasons a condition can show no gaps: it has
        # none by design (spread kills each node once), every run was voided,
        # or there are no runs yet. Collapsing them into one message would
        # read as a property of the condition when it is a property of the data.
        n_live = sum(1 for r in live if r["condition"] == c)
        if g:
            why = (f"{min(g):.2f}-{max(g):.2f}s  "
                   f"median {statistics.median(g):.2f}s")
        elif c == "spread":
            why = "none by construction -- each node killed once"
        elif n_live == 0:
            why = "no live runs (all voided or none present)"
        else:
            why = "live runs present but no same-node repeats recorded"
        print(f"    {c:<22} n={len(g):>3}  {why}")
    s, l = spans["short-gap-same-node"], spans["long-gap-same-node"]
    if s and l:
        overlap = not (s[1] < l[0] or l[1] < s[0])
        print(f"    short/long overlap in realized terms: "
              f"{'YES -- conditions contaminated' if overlap else 'no -- cleanly separated'}")
        if overlap:
            warn.append("short and long conditions overlap in realized spacing")

    # ------------------------------------------------------------- per-run heal
    print("\n" + "=" * 74)
    print("Per-run healing (absolute missing ids, per DECISION_LOG's dilution trap)")
    print("=" * 74)
    print(f"{'condition':<22}{'seed':>10}{'kills':>6}{'damage':>9}"
          f"{'residual':>10}{'recovered':>11}{'healed':>8}{'wfail%':>8}")
    by_cond = {c: [] for c in CONDITIONS}
    for r in sorted(live, key=lambda x: (x["condition"] or "", x["seed"] or 0)):
        h = r["heal"]
        if h is None:
            print(f"{str(r['condition']):<22}{str(r['seed']):>10}{r['n_kills']:>6}"
                  f"{'no quiesce window -- not scorable':>38}")
            continue
        pre = h["pre"]["missing"]
        at_stop = (h["post0_30"] if h["post0_30"]["n"] else None)
        damage = (at_stop["missing"] - pre) if at_stop else float("nan")
        rec = h["recovered_frac"]
        by_cond[r["condition"]].append({
            "seed": r["seed"], "damage": damage,
            "residual": h["residual_missing"], "recovered": rec,
            "healed": h["healed"], "wfail": r["write_fail_rate"],
        })
        print(f"{r['condition']:<22}{r['seed']:>10}{r['n_kills']:>6}"
              f"{fmt(damage, 0):>9}{fmt(h['residual_missing'], 0):>10}"
              f"{(fmt(rec * 100, 0) + '%') if not np.isnan(rec) else '--':>11}"
              f"{str(h['healed']):>8}{fmt(r['write_fail_rate'] * 100, 2):>8}")

    # ------------------------------------------------------------ per-condition
    print("\n" + "=" * 74)
    print("By condition")
    print("=" * 74)
    print(f"{'condition':<22}{'n':>4}{'recovered mean':>16}{'residual mean':>15}"
          f"{'healed':>9}{'wfail% mean':>13}")
    for c in CONDITIONS:
        rs = by_cond[c]
        if not rs:
            print(f"{c:<22}{'0':>4}   no scorable runs")
            continue
        recs = [r["recovered"] for r in rs if not np.isnan(r["recovered"])]
        res = [r["residual"] for r in rs if not np.isnan(r["residual"])]
        wf = [r["wfail"] for r in rs if not np.isnan(r["wfail"])]
        healed = sum(1 for r in rs if r["healed"])
        print(f"{c:<22}{len(rs):>4}"
              f"{(fmt(statistics.mean(recs) * 100, 0) + '%') if recs else '--':>16}"
              f"{fmt(statistics.mean(res), 0) if res else '--':>15}"
              f"{f'{healed}/{len(rs)}':>9}"
              f"{fmt(statistics.mean(wf) * 100, 2) if wf else '--':>13}")

    # -------------------------------------------------------------- comparisons
    print("\n" + "=" * 74)
    print("Between-condition comparisons (exact two-sided Mann-Whitney, 5v5 floor "
          "p=0.0079)")
    print("=" * 74)
    pairs = [("short-gap-same-node", "long-gap-same-node"),
             ("short-gap-same-node", "spread"),
             ("long-gap-same-node", "spread")]
    for metric, key, better in (("recovered fraction", "recovered", "higher"),
                                ("residual missing", "residual", "lower"),
                                ("write-failure rate", "wfail", "lower")):
        print(f"\n  {metric} ({better} is healthier)")
        for a, b in pairs:
            va = [r[key] for r in by_cond[a] if not np.isnan(r[key])]
            vb = [r[key] for r in by_cond[b] if not np.isnan(r[key])]
            if len(va) < 2 or len(vb) < 2:
                print(f"    {a} vs {b}: not enough scorable runs "
                      f"({len(va)} vs {len(vb)})")
                continue
            u, p = mann_whitney(va, vb)
            star = "*" if p < 0.05 else " "
            print(f"    {a:<22} {statistics.mean(va):>9.3f}  vs  "
                  f"{b:<20} {statistics.mean(vb):>9.3f}   U={u:<6.1f} p={p:.4f}{star}")

    print("\n" + "=" * 74)
    print("Per-seed values, since means at n=5 hide how much they overlap")
    print("=" * 74)
    for c in CONDITIONS:
        rs = sorted(by_cond[c], key=lambda r: r["seed"] or 0)
        if rs:
            print(f"  {c:<22} recovered "
                  f"{[round(r['recovered'], 3) if not np.isnan(r['recovered']) else None for r in rs]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
