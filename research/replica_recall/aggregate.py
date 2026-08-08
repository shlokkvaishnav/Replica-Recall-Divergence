"""
Aggregate a seed sweep: baseline vs chaos, across runs.

Two point estimates cannot tell you whether an effect is real. This reads
every run under results_sweep/, reduces each to one row of numbers, and
compares the two conditions across seeds with an exact rank test.

Usage:
    python research/replica_recall/aggregate.py
    python research/replica_recall/aggregate.py --sweep-dir path/to/results_sweep
"""

from __future__ import annotations

import argparse
import math
import os
import re
import sys
from itertools import combinations

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from analyze import load, summarize_run, heal_stats, SIZE_BIN, fmt        # noqa: E402


# ---------------------------------------------------------------------------
# Exact Mann-Whitney U
# ---------------------------------------------------------------------------

def mann_whitney(a: list[float], b: list[float]) -> tuple[float, float]:
    """Two-sided Mann-Whitney U with an exact null for small samples.

    Rank-based rather than a t-test: five runs per condition is far too few
    to lean on normality, and the metrics here are bounded proportions.

    Returns (U, p). Falls back to the normal approximation when the exact
    enumeration would be too large.
    """
    a = [x for x in a if not np.isnan(x)]
    b = [x for x in b if not np.isnan(x)]
    n1, n2 = len(a), len(b)
    if n1 == 0 or n2 == 0:
        return float("nan"), float("nan")

    def _u(xs, ys):
        return (sum(1.0 for x in xs for y in ys if x > y)
                + 0.5 * sum(1.0 for x in xs for y in ys if x == y))

    u_obs = _u(a, b)
    centre = n1 * n2 / 2.0

    if n1 + n2 <= 20:
        pool = sorted(a + b)
        idx = range(n1 + n2)
        extreme = 0
        total = 0
        for combo in combinations(idx, n1):
            sel = set(combo)
            av = [pool[i] for i in combo]
            bv = [pool[i] for i in idx if i not in sel]
            if abs(_u(av, bv) - centre) >= abs(u_obs - centre) - 1e-12:
                extreme += 1
            total += 1
        return u_obs, extreme / total

    sd = np.sqrt(n1 * n2 * (n1 + n2 + 1) / 12.0)
    if sd == 0:
        return u_obs, float("nan")
    z = (abs(u_obs - centre) - 0.5) / sd          # continuity correction
    p = math.erfc(z / math.sqrt(2.0))             # two-sided normal tail
    return u_obs, float(min(1.0, p))


# ---------------------------------------------------------------------------

RUN_RE = re.compile(r"^seed(\d+)_(baseline|chaos|quiesce)$")


def discover(sweep_dir: str) -> dict[str, list[tuple[int, dict]]]:
    if not os.path.isdir(sweep_dir):
        print(f"ERROR: {sweep_dir} not found. Run sweep.py first.",
              file=sys.stderr)
        sys.exit(1)

    out: dict[str, list[tuple[int, dict]]] = {
        "baseline": [], "chaos": [], "quiesce": []}
    for name in sorted(os.listdir(sweep_dir)):
        m = RUN_RE.match(name)
        if not m:
            continue
        path = os.path.join(sweep_dir, name)
        if not os.path.exists(os.path.join(path, "samples.csv")):
            print(f"  (skipping {name}: no samples.csv)")
            continue
        rows, _, meta = load(path)
        s = summarize_run(rows)
        s["heal"] = heal_stats(rows, meta)
        out[m.group(2)].append((int(m.group(1)), s))
    return out


def report_healing(runs) -> None:
    """Pool the quiesce runs: does completeness come back after faults stop?"""
    quiesce = [(s, r["heal"]) for s, r in runs["quiesce"] if r.get("heal")]
    if not quiesce:
        return

    print("\n" + "=" * 78)
    print("Healing -- quiesce runs (faults stopped mid-run)")
    print("=" * 78)
    print(f"  {'seed':>10} {'pre':>9} {'during':>9} {'after':>9} "
          f"{'deficit':>9} {'healed':>8}")
    print("  " + "-" * 60)

    pre_all, post_all, deficits, healed_flags = [], [], [], []
    for seed, h in quiesce:
        last = next((h[k] for k in ("post60p", "post30_60", "post0_30")
                     if h[k]["n"] > 0), None)
        if last is None:
            continue
        pre_c = h["pre"]["completeness"]
        dur_c = h["during"]["completeness"]
        pre_all.append(pre_c)
        post_all.append(last["completeness"])
        deficits.append(h["deficit"])
        healed_flags.append(bool(h["healed"]))
        print(f"  {seed:>10} {fmt(pre_c):>9} {fmt(dur_c):>9} "
              f"{fmt(last['completeness']):>9} {fmt(h['deficit']):>9} "
              f"{('yes' if h['healed'] else 'NO'):>8}")

    if not deficits:
        return
    print()
    print(f"  mean pre-chaos completeness  : {fmt(float(np.mean(pre_all)))}")
    print(f"  mean post-chaos completeness : {fmt(float(np.mean(post_all)))}")
    print(f"  residual deficit             : {fmt(float(np.mean(deficits)))} "
          f"(range {fmt(min(deficits))} to {fmt(max(deficits))})")
    print()
    # No p-value here on purpose. Pre and post are paired within a run, and
    # pre is 1.0000 in every healthy run -- an unpaired rank test on a
    # constant would report the floor regardless of the effect and would be
    # meaningless. The deficit and its range are the honest summary; the
    # per-run healed/NO column is the finding.
    n_healed = sum(healed_flags)
    n_runs = len(healed_flags)
    if n_healed == n_runs:
        print(f"  HEALS in {n_healed}/{n_runs} runs -- completeness returns to")
        print("  its pre-chaos level. The divergence is transient, and the")
        print("  permanent-degradation claim does not hold.")
    elif n_healed == 0:
        print(f"  DOES NOT HEAL in any of {n_runs} runs. Replicas that missed")
        print("  writes never recover them: no anti-entropy, no read-repair,")
        print("  no catch-up. This is the reportable result.")
    else:
        print(f"  MIXED: healed in {n_healed}/{n_runs} runs. Worth understanding")
        print("  what differs between them before claiming either way.")


METRICS = [
    ("spread_mean",   "within-shard spread", "higher = replicas disagree more"),
    ("spread_p95",    "  p95 spread",        ""),
    ("hit_rate",      "detector hit rate",   "vs chance 0.333"),
    ("rank_corr",     "  rank correlation",  ""),
    ("margin",        "  true recall margin", ""),
    ("index_recall",  "index_recall",        "graph quality"),
    ("completeness",  "completeness",        "data content"),
    ("e2e_recall",    "e2e_recall",          "what a client sees"),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sweep-dir", default=os.path.join(HERE, "results_sweep"))
    args = ap.parse_args()

    runs = discover(args.sweep_dir)
    nb, nc = len(runs["baseline"]), len(runs["chaos"])
    nq = len(runs["quiesce"])

    print("=" * 78)
    print("Seed sweep -- baseline vs chaos")
    print("=" * 78)
    print(f"  baseline runs : {nb}   seeds {[s for s, _ in runs['baseline']]}")
    print(f"  chaos runs    : {nc}   seeds {[s for s, _ in runs['chaos']]}")
    if nq:
        print(f"  quiesce runs  : {nq}   seeds {[s for s, _ in runs['quiesce']]}")

    if nb < 2 or nc < 2:
        print("\n  Need at least 2 baseline and 2 chaos runs for the")
        print("  condition comparison. Run sweep.py.")
        # Quiesce runs stand on their own -- report them even when the
        # baseline/chaos comparison cannot be made yet.
        report_healing(runs)
        return 1

    print()
    print(f"  {'metric':<24} {'baseline':>18} {'chaos':>18} {'p':>8}")
    print("  " + "-" * 72)

    results = {}
    for key, label, note in METRICS:
        bv = [s[key] for _, s in runs["baseline"]]
        cv = [s[key] for _, s in runs["chaos"]]
        bva = [x for x in bv if not np.isnan(x)]
        cva = [x for x in cv if not np.isnan(x)]
        if not bva or not cva:
            print(f"  {label:<24} {'n/a':>18} {'n/a':>18} {'':>8}")
            continue
        _, p = mann_whitney(bv, cv)
        results[key] = p
        bs = f"{np.mean(bva):.4f} +/- {np.std(bva, ddof=1) if len(bva) > 1 else 0:.4f}"
        cs = f"{np.mean(cva):.4f} +/- {np.std(cva, ddof=1) if len(cva) > 1 else 0:.4f}"
        star = "*" if p <= 0.05 else " "
        print(f"  {label:<24} {bs:>18} {cs:>18} {p:>7.4f}{star}")

    p_floor = mann_whitney(list(range(nb)), [x + 100 for x in range(nc)])[1]
    print()
    print(f"  Exact two-sided Mann-Whitney. With {nb} vs {nc} runs the smallest")
    print(f"  attainable p is {p_floor:.4f} -- reaching it means the two groups")
    print("  separate completely, not that the effect is enormous.")

    # ---- size-matched recall ------------------------------------------------
    print("\n" + "=" * 78)
    print("Recall by index size, pooled across seeds")
    print("=" * 78)

    def pooled(cond):
        acc: dict[int, list[float]] = {}
        for _, s in runs[cond]:
            for b, (e, _i, _n) in s["by_size"].items():
                acc.setdefault(b, []).append(e)
        return acc

    pb, pc = pooled("baseline"), pooled("chaos")
    keys = sorted(set(pb) | set(pc))
    print(f"  {'index size':>18} {'baseline':>10} {'chaos':>10} {'delta':>9} "
          f"{'runs':>10}")
    print("  " + "-" * 62)
    deltas = []
    for k in keys:
        b = float(np.mean(pb[k])) if k in pb else float("nan")
        c = float(np.mean(pc[k])) if k in pc else float("nan")
        d = c - b if not (np.isnan(b) or np.isnan(c)) else float("nan")
        if not np.isnan(d):
            deltas.append(d)
        lo, hi = k * SIZE_BIN, (k + 1) * SIZE_BIN
        cnt = f"{len(pb.get(k, []))}/{len(pc.get(k, []))}"
        print(f"  {lo:>7,}-{hi:<9,} {fmt(b):>10} {fmt(c):>10} {fmt(d):>9} "
              f"{cnt:>10}")

    if deltas:
        print()
        print(f"  mean size-matched delta: {fmt(float(np.mean(deltas)))} "
              f"over {len(deltas)} bins")

    report_healing(runs)

    # ---- verdict ------------------------------------------------------------
    print("\n" + "=" * 78)
    print("Verdict")
    print("=" * 78)

    sp = results.get("spread_mean", float("nan"))
    hr = results.get("hit_rate", float("nan"))
    ir = results.get("index_recall", float("nan"))
    cp = results.get("completeness", float("nan"))

    def _say(ok, yes, no):
        print(("  [yes] " if ok else "  [no ] ") + (yes if ok else no))

    _say(not np.isnan(sp) and sp <= 0.05,
         "Replicas diverge more under failure than without it.",
         "Divergence under failure is not separable from baseline noise.")
    _say(not np.isnan(hr) and hr <= 0.05,
         "The ground-truth-free detector separates the two conditions.",
         "The detector does not separate the two conditions.")
    _say(not np.isnan(ir) and ir > 0.05,
         "index_recall is NOT distinguishable -- failure does not degrade "
         "the graph.",
         "index_recall differs between conditions -- failure may be degrading "
         "the graph, which would complicate the data-loss story.")
    _say(not np.isnan(cp) and cp <= 0.05,
         "completeness IS distinguishable -- the divergence is data loss.",
         "completeness does not separate -- the mechanism is unclear.")

    print()
    print("  The intended shape of the result is all four above reading [yes]:")
    print("  replicas diverge, the detector sees it, the graph is fine, and")
    print("  the cause is missing data. Any [no] is worth understanding before")
    print("  writing anything up.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
