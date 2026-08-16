"""
Paired forensics: what does failure actually do to the graph?

The replica-recall experiment establishes *that* node-kill chaos degrades
index_recall with the data held constant. This establishes *what* the damage
is, by running matched baseline and chaos clusters and dissecting the index
files each one leaves behind.

Why a separate driver rather than a flag on sweep.py: the index files live in
chaos_run/, which every run deletes and recreates. Reading them while a run is
in flight measures write-in-progress, not damage -- the header's element_count
lags the node bodies, links point at nodes not yet flushed, and both look
exactly like corruption. So each run must be torn down before its files are
read, and the analysis has to happen before the next run wipes them.

Results are kept as JSON rather than by copying index files: six 100 MB
replicas per run is 600 MB of mostly zeroes, and the statistics are what get
compared.

    python research/replica_recall/forensics_experiment.py --seeds 3
    python research/replica_recall/forensics_experiment.py --report   # re-print

Interpreting it: replicas within a shard are supposed to hold identical data,
so a baseline-vs-chaos difference in link quality is graph damage that is not
explained by missing vectors -- which is the mechanism the finding needs.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)

import chaos_harness as ch                                        # noqa: E402
from graph_forensics import analyse, load_index, link_quality     # noqa: E402

RUNNER = os.path.join(HERE, "run_experiment.py")
OUT_JSON = os.path.join(HERE, "forensics_results.json")

# Metrics worth a column. Structural ones first, then the semantic one.
REPORT = [
    ("nodes_examined", "nodes written", "{:>10,.0f}"),
    ("uncounted_nodes", "written but uncounted", "{:>10,.0f}"),
    ("in_degree_0", "in-degree 0 (invisible)", "{:>10,.0f}"),
    ("unreachable_from_entry", "unreachable from entry", "{:>10,.0f}"),
    ("out_degree_0", "out-degree 0", "{:>10,.0f}"),
    ("dangling_edges", "dangling edges", "{:>10,.0f}"),
    ("deg0_mean", "mean layer-0 degree", "{:>10.2f}"),
    ("asymmetric_frac", "asymmetric edges", "{:>10.4%}"),
    ("link_quality", "LINK QUALITY", "{:>10.4f}"),
    ("link_quality_p5", "  p5", "{:>10.4f}"),
]


def run_one(seed: int, cond: str, duration: int, writers: int,
            dist: str, sample: int, extra: list[str]) -> list[dict]:
    """One cluster lifecycle, then dissect what it left on disk."""
    cmd = [sys.executable, RUNNER,
           "--duration", str(duration), "--writers", str(writers),
           "--seed", str(seed), "--dist", dist]
    if cond == "baseline":
        cmd.append("--no-chaos")
    cmd += extra

    t0 = time.time()
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    dt = time.time() - t0
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-3:]
        print(f"  seed {seed} {cond:<8} FAILED: {' | '.join(tail)}")
        return []

    # The cluster is down now, so the files are quiescent and readable.
    data_dir = os.path.join(ch.RUN_DIR, "data")
    out = []
    for name in sorted(os.listdir(data_dir)):
        d = os.path.join(data_dir, name)
        if not os.path.exists(os.path.join(d, "index.ndb")):
            continue
        try:
            r = analyse(d)
            _, nodes, _ = load_index(d)
            r.update(link_quality(nodes, sample=sample, seed=seed))
            r.update({"seed": seed, "cond": cond, "replica": name,
                      "shard": name.split("-")[1] if "-" in name else "?"})
            out.append(r)
        except Exception as e:
            print(f"  {name}: analysis failed ({e})")

    lq = [r.get("link_quality") for r in out if r.get("link_quality") is not None]
    print(f"  seed {seed} {cond:<8} ok  {len(out)} replicas  {dt:5.0f}s"
          + (f"  link_quality {statistics.mean(lq):.4f}" if lq else ""))
    return out


def report(results: list[dict]) -> None:
    conds = [c for c in ("baseline", "chaos") if any(r["cond"] == c for r in results)]
    if not conds:
        print("no results")
        return

    print("\n" + "=" * 74)
    print("Graph forensics -- baseline vs chaos, matched runs")
    print("=" * 74)
    n_runs = {c: len({r["seed"] for r in results if r["cond"] == c}) for c in conds}
    print("  " + "  ".join(f"{c}: {n_runs[c]} seeds x "
                           f"{sum(1 for r in results if r['cond'] == c)} replicas"
                           for c in conds))
    print()
    print(f"  {'metric':<26}" + "".join(f"{c:>12}" for c in conds) + f"{'delta':>12}")
    print("  " + "-" * 62)

    for key, label, spec in REPORT:
        vals = {}
        for c in conds:
            xs = [r[key] for r in results
                  if r["cond"] == c and r.get(key) is not None]
            vals[c] = statistics.mean(xs) if xs else None
        cells = "".join(
            (spec.format(vals[c]) if vals[c] is not None else f"{'-':>10}") + "  "
            for c in conds)
        if len(conds) == 2 and all(vals[c] is not None for c in conds):
            d = vals["chaos"] - vals["baseline"]
            delta = f"{d:>+12.4f}" if abs(d) < 100 else f"{d:>+12,.0f}"
        else:
            delta = f"{'-':>12}"
        print(f"  {label:<26}{cells}{delta}")

    # The headline comparison, spelled out. Link quality holds data constant
    # by construction -- ground truth comes from each replica's own contents --
    # so a gap here is graph damage that missing vectors do not explain.
    base = [r["link_quality"] for r in results
            if r["cond"] == "baseline" and r.get("link_quality") is not None]
    chao = [r["link_quality"] for r in results
            if r["cond"] == "chaos" and r.get("link_quality") is not None]
    if base and chao:
        print()
        print(f"  link quality  baseline {statistics.mean(base):.4f}"
              f"  (n={len(base)}, sd {statistics.pstdev(base):.4f})")
        print(f"                chaos    {statistics.mean(chao):.4f}"
              f"  (n={len(chao)}, sd {statistics.pstdev(chao):.4f})")
        gap = statistics.mean(base) - statistics.mean(chao)
        spread = max(statistics.pstdev(base), statistics.pstdev(chao))
        sd_note = f"   ({gap / spread:+.1f} sd)" if spread else ""
        print(f"                gap      {gap:+.4f}{sd_note}")
        if gap <= 0:
            print("\n  No degradation in link quality. If index_recall still")
            print("  separates, the mechanism is something this does not")
            print("  measure -- report that rather than reaching for another")
            print("  metric until one moves.")


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seeds", type=int, default=3)
    ap.add_argument("--seed-base", type=int, default=20260808)
    ap.add_argument("--duration", type=int, default=300)
    ap.add_argument("--writers", type=int, default=4)
    ap.add_argument("--dist", default="sift")
    ap.add_argument("--sample", type=int, default=2000,
                    help="nodes per replica scored for link quality")
    ap.add_argument("--report", action="store_true",
                    help="re-print from saved results, run nothing")
    args, extra = ap.parse_known_args()

    if args.report:
        if not os.path.exists(OUT_JSON):
            print(f"no results at {OUT_JSON}", file=sys.stderr)
            return 1
        report(json.load(open(OUT_JSON)))
        return 0

    if not os.path.exists(ch.SHARD_NODE_BIN):
        print("ERROR: binaries not found. Build first.", file=sys.stderr)
        return 1

    seeds = [args.seed_base + i for i in range(args.seeds)]
    total = len(seeds) * 2
    print(f"[forensics] {len(seeds)} seeds x 2 conditions = {total} runs, "
          f"~{total * (args.duration + 45) / 60:.0f} min")
    print(f"[forensics] corpus={args.dist}  link-quality sample={args.sample}")
    print()

    results: list[dict] = []
    for seed in seeds:
        for cond in ("baseline", "chaos"):
            results += run_one(seed, cond, args.duration, args.writers,
                               args.dist, args.sample, extra)
            # Written after every run: these are expensive to reproduce and a
            # crash on run 5 should not cost runs 1-4.
            with open(OUT_JSON, "w") as f:
                json.dump(results, f, indent=2)

    report(results)
    print(f"\nwrote {OUT_JSON}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
