#!/usr/bin/env python3
"""
Analysis for the indexing-gate pilot (issue #28). No cluster needed.

Reads every run directory under results/ (each with run_meta.json and
telemetry.csv) and computes SPEC.md's metrics per run:

  primary
    gate_close_s      seconds the gate took to close after writers paused
    base_frac_1p0     fraction of BASELINE-window telemetry samples where the
                      worst replica is 1.0 indexed
    chaos_frac_0p95   fraction of CHAOS-window samples where the worst replica
                      is >= 0.95 indexed (None for --no-chaos runs)
  secondary
    setup_s           wall-clock the gate added (== gate_close_s; kept as its
                      own column because the spec asks for it by that name)
    probe_s_median    median sampler probe cost, to see whether an indexed
                      corpus changed what a search costs (outcome (d))
    reindex_s         --chaos runs only: per restart, seconds until that node
                      is back to >= 0.95 indexed

Written before the sweep ran, per GIT_WORKFLOW.md and SPEC.md, so the numbers
that decide the outcome are computed by code that did not know them.

Usage:
    python research/qdrant_index_gate/analyze_gate.py [results_dir]
"""
from __future__ import annotations

import csv
import json
import os
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "results")


def min_fraction_per_sample(telemetry_rows):
    """Group telemetry by t_rel; return [(t_rel, min over nodes of
    indexed/points)]. A node with points 0 or missing counts as 0.0."""
    by_t: dict[float, list[float]] = {}
    for r in telemetry_rows:
        try:
            t = float(r["t_rel"])
            idx = float(r["indexed_vectors_count"] or 0)
            pts = float(r["points_count"] or 0)
        except (KeyError, ValueError):
            continue
        f = min(1.0, idx / pts) if pts > 0 else 0.0
        by_t.setdefault(t, []).append(f)
    return sorted((t, min(v)) for t, v in by_t.items())


def window_fraction(samples, lo, hi, thresh):
    inwin = [f for t, f in samples if lo <= t < hi]
    if not inwin:
        return None, 0
    return sum(1 for f in inwin if f >= thresh) / len(inwin), len(inwin)


def analyze_run(d: str) -> dict | None:
    meta_p = os.path.join(d, "run_meta.json")
    tele_p = os.path.join(d, "telemetry.csv")
    samp_p = os.path.join(d, "samples.csv")
    fail_p = os.path.join(d, "index_gate_failed.json")
    if not os.path.exists(meta_p):
        if not os.path.exists(fail_p):
            return None
        # A gate that never closed is a result for this pilot (SPEC.md
        # Amendment 1): say WHICH way it failed. "never-indexed" = no replica
        # ever reported an indexed vector (below Qdrant's segment threshold,
        # outcome (c)); "plateau" = every replica reached a stable fraction
        # below tol -- the appendable-segment tail, outcome (b).
        g = json.load(open(fail_p))
        name = os.path.basename(d)
        thr = None
        if name.startswith("thr") and not name.startswith("thrdefault"):
            try:
                thr = int(name.split("_", 1)[0][3:])
            except ValueError:
                pass
        mf = g.get("min_fraction_at_end") or 0.0
        return {
            "run": name, "threshold_kb": thr, "gated": True,
            "gate_closed": False, "gate_close_s": None, "setup_s": g.get("elapsed_s"),
            "min_frac_overall": mf,
            "gate_fail_mode": "never-indexed" if mf == 0.0 else f"plateau@{mf:.4f}",
            "chaos": "chaos" in name,
        }
    meta = json.load(open(meta_p))
    out = {
        "run": os.path.basename(d),
        "run_id": meta.get("run_id"),
        "threshold_kb": meta.get("indexing_threshold_kb"),
        "sift_vectors": meta.get("sift_vectors"),
        "chaos": meta.get("chaos"),
        "gated": meta.get("index_gate") is not None,
    }
    g = meta.get("index_gate") or {}
    out["gate_closed"] = g.get("closed")
    out["gate_close_s"] = g.get("elapsed_s")
    out["setup_s"] = g.get("elapsed_s")
    out["gate_closed_rel"] = g.get("gate_closed_rel")

    if not os.path.exists(tele_p):
        out["note"] = "no telemetry.csv (run without --capture-telemetry)"
        return out
    tele = list(csv.DictReader(open(tele_p)))
    samples = min_fraction_per_sample(tele)
    end = float(meta.get("duration_s", 0)) + float(meta.get("warmup_s", 0)) + \
        float(g.get("elapsed_s") or 0) + 1e6  # open-ended upper bound
    t0 = float(g.get("gate_closed_rel") or meta.get("warmup_s") or 0)
    cs, ce = meta.get("chaos_start_rel"), meta.get("chaos_stop_rel")
    if meta.get("chaos") and cs is not None:
        out["base_frac_1p0"], out["base_n"] = window_fraction(samples, t0, cs, 1.0)
        out["chaos_frac_0p95"], out["chaos_n"] = window_fraction(samples, cs, ce or end, 0.95)
        out["chaos_frac_1p0"], _ = window_fraction(samples, cs, ce or end, 1.0)
    else:
        out["base_frac_1p0"], out["base_n"] = window_fraction(samples, t0, end, 1.0)
        out["chaos_frac_0p95"], out["chaos_n"] = None, 0
    out["min_frac_overall"] = min((f for _, f in samples), default=None)

    if os.path.exists(samp_p):
        probe = []
        for r in csv.DictReader(open(samp_p)):
            try:
                probe.append(float(r["probe_s"]))
            except (KeyError, ValueError):
                pass
        out["probe_s_median"] = round(statistics.median(probe), 3) if probe else None

    # Re-index time after each restart: from events.json, for each event find
    # the first later telemetry sample where that node is back >= 0.95.
    ev_p = os.path.join(d, "events.json")
    if meta.get("chaos") and os.path.exists(ev_p):
        node_of = {}
        for r in tele:
            try:
                node_of.setdefault(int(r["node"]), []).append(
                    (float(r["t_rel"]),
                     (float(r["indexed_vectors_count"] or 0) / float(r["points_count"]))
                     if float(r["points_count"] or 0) > 0 else 0.0))
            except (KeyError, ValueError, ZeroDivisionError):
                pass
        reindex = []
        for e in json.load(open(ev_p)):
            name = e.get("target", "")
            try:
                n = int(name.rsplit("node", 1)[-1])
            except ValueError:
                continue
            t_up = float(e.get("t_rel", 0)) + float(e.get("down_for_s", 0))
            later = [(t, f) for t, f in node_of.get(n, []) if t >= t_up]
            back = next((t for t, f in later if f >= 0.95), None)
            reindex.append(None if back is None else round(back - t_up, 1))
        out["reindex_s"] = reindex
    return out


def main() -> int:
    if not os.path.isdir(RESULTS):
        print(f"no results dir: {RESULTS}")
        return 1
    runs = [analyze_run(os.path.join(RESULTS, d)) for d in sorted(os.listdir(RESULTS))
            if os.path.isdir(os.path.join(RESULTS, d))]
    runs = [r for r in runs if r]
    if not runs:
        print("no runs found")
        return 1
    cols = ["run", "threshold_kb", "sift_vectors", "chaos", "gated", "gate_closed",
            "gate_close_s", "gate_fail_mode", "base_frac_1p0", "base_n",
            "chaos_frac_0p95", "chaos_n", "min_frac_overall", "probe_s_median",
            "reindex_s"]
    print("\t".join(cols))
    for r in runs:
        print("\t".join("" if r.get(c) is None else str(r.get(c)) for c in cols))

    # Outcome bookkeeping, mechanically, per SPEC.md's Expected outcomes.
    gated = [r for r in runs if r["gated"]]
    print()
    print(f"{len(runs)} runs, {len(gated)} gated, "
          f"{sum(1 for r in gated if r['gate_closed'])} gates closed")
    for thr in sorted({r["threshold_kb"] for r in gated}, key=lambda x: (x is None, x)):
        rs = [r for r in gated if r["threshold_kb"] == thr]
        cl = [r["gate_close_s"] for r in rs if r["gate_closed"]]
        base = [r["base_frac_1p0"] for r in rs if r.get("base_frac_1p0") is not None]
        chaos = [r["chaos_frac_0p95"] for r in rs if r.get("chaos_frac_0p95") is not None]
        modes = sorted({r.get("gate_fail_mode") for r in rs if not r["gate_closed"]} - {None})
        print(f"  threshold {thr!s:>7}: {len(cl)}/{len(rs)} closed"
              f"{' (fail: ' + ', '.join(modes) + ')' if modes else ''}, "
              f"close_s range {min(cl) if cl else None}-{max(cl) if cl else None}, "
              f"baseline@1.0 range {min(base) if base else None}-{max(base) if base else None}, "
              f"chaos@0.95 range {min(chaos) if chaos else None}-{max(chaos) if chaos else None}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
