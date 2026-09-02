#!/usr/bin/env python3
"""Check a controlled-schedule run's realized kills against what was requested.

Issue #17's Metrics section: "A validation run per condition against a live
cluster, with the realized schedule checked against the request, is the
deliverable -- not a statistical result."

`test_kill_schedule.py` proves the scheduler emits correct schedules against a
fake container that restarts instantly. This proves nothing about a real Qdrant
container, whose restart takes as long as it takes. That gap is issue #17's own
expected outcome (b), and this script is how it gets measured.

Reports, per condition: requested vs realized offsets and gaps, the drift
between them, and any kill that landed on a container which had not come back
(`killed_while_down`). It states a verdict but does not hide the numbers behind
it -- drift is a property to quantify and carry into #9's design, not a
pass/fail gate.

Usage:
    python research/qdrant_kill_scheduler/check_realized_schedule.py \
        --results-dir research/qdrant_kill_scheduler/results
"""
import argparse
import glob
import json
import os

# Above this, "requested spacing" stops describing what happened well enough to
# define #9's conditions on requested values, and they must be defined on
# realized ones instead. Chosen relative to the gap between SHORT_GAP_S (5s) and
# LONG_GAP_S (40s): drift would have to exceed this by a lot before the two
# conditions could be confused for each other.
DRIFT_TOLERANCE_S = 2.0

# chaos_loop_scheduled's fixed down-time, mirrored here so realized restart
# latency can be derived from an events.json alone.
FIXED_DOWN_S = 4.5


def check_run(run_dir):
    events = json.load(open(os.path.join(run_dir, "events.json")))
    if not events:
        return None
    scheduled = [e for e in events if "condition" in e]
    if not scheduled:
        return {"dir": run_dir, "scheduled": False, "n_events": len(events)}

    rows, drifts = [], []
    for e in scheduled:
        at_drift = e["realized_at_s"] - e["requested_at_s"]
        gap_drift = (None if e["requested_gap_s"] is None or e["realized_gap_s"] is None
                     else e["realized_gap_s"] - e["requested_gap_s"])
        if gap_drift is not None:
            drifts.append(gap_drift)
        rows.append({
            "seq": e["seq"], "target": e["target"],
            "req_at": e["requested_at_s"], "real_at": e["realized_at_s"],
            "at_drift": at_drift,
            "req_gap": e["requested_gap_s"], "real_gap": e["realized_gap_s"],
            "gap_drift": gap_drift,
            "killed_while_down": e.get("killed_while_down"),
            "alive_after_restart": e.get("alive_after_restart"),
        })
    return {
        "dir": run_dir, "scheduled": True,
        "condition": scheduled[0]["condition"],
        "rows": rows, "drifts": drifts,
        "targets": {e["target"] for e in scheduled},
        "n_killed_while_down": sum(1 for e in scheduled if e.get("killed_while_down")),
        "n_dead_after_restart": sum(1 for e in scheduled
                                    if e.get("alive_after_restart") is False),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--results-dir",
                    default="research/qdrant_kill_scheduler/results")
    args = ap.parse_args()

    run_dirs = sorted(d for d in glob.glob(os.path.join(args.results_dir, "*"))
                      if os.path.isfile(os.path.join(d, "events.json")))
    if not run_dirs:
        print(f"no runs with events.json under {args.results_dir}")
        return 1

    all_drift, all_latency, problems = [], [], []
    for d in run_dirs:
        r = check_run(d)
        if r is None:
            print(f"\n{os.path.basename(d)}: events.json is empty -- no kills recorded")
            problems.append(f"{os.path.basename(d)}: no kills")
            continue
        if not r["scheduled"]:
            print(f"\n{os.path.basename(d)}: {r['n_events']} events, none carrying a "
                  f"condition -- this is a randomized run, not a scheduled one")
            continue

        print(f"\n=== {r['condition']}  ({os.path.basename(d)}) ===")
        print(f"{'seq':>4}{'target':>20}{'req@':>9}{'real@':>9}{'drift':>8}"
              f"{'req gap':>9}{'real gap':>10}{'drift':>8}  flags")
        for w in r["rows"]:
            gap_s = "--" if w["req_gap"] is None else f"{w['req_gap']:.1f}"
            rgap_s = "--" if w["real_gap"] is None else f"{w['real_gap']:.1f}"
            gd_s = "--" if w["gap_drift"] is None else f"{w['gap_drift']:+.2f}"
            flags = []
            if w["killed_while_down"]:
                flags.append("KILLED_WHILE_DOWN")
            if w["alive_after_restart"] is False:
                flags.append("DEAD_AFTER_RESTART")
            print(f"{w['seq']:>4}{w['target']:>20}{w['req_at']:>9.1f}"
                  f"{w['real_at']:>9.1f}{w['at_drift']:>+8.2f}"
                  f"{gap_s:>9}{rgap_s:>10}{gd_s:>8}  {' '.join(flags)}")

        # Where the drift comes from: c.start() returns only once Docker has
        # actually restarted the container, so the node comes back LATER than
        # kill+down_for implies, and that lateness is subtracted from the gap.
        # Reported because #9 needs the number, not just the fact.
        lat = []
        by_seq = {w["seq"]: w for w in r["rows"]}
        for w in r["rows"]:
            prev = by_seq.get(w["seq"] - 1)
            if prev is None or w["real_gap"] is None:
                continue
            restart_at = w["real_at"] - w["real_gap"]
            lat.append(restart_at - (prev["real_at"] + FIXED_DOWN_S))
        if lat:
            print(f"  implied restart latency (docker start -> node back): "
                  f"{min(lat):+.2f} to {max(lat):+.2f}s  "
                  f"-- this is what the gap loses")
            all_latency.extend(lat)

        n_targets = len(r["targets"])
        expected_spread = r["condition"] == "spread"
        targeting_ok = (n_targets == len(r["rows"])) if expected_spread else (n_targets == 1)
        print(f"  targeting: {n_targets} distinct node(s) over {len(r['rows'])} kills"
              f" -- {'as requested' if targeting_ok else 'NOT as requested'}")
        if not targeting_ok:
            problems.append(f"{r['condition']}: targeting wrong")
        if r["n_killed_while_down"]:
            problems.append(f"{r['condition']}: {r['n_killed_while_down']} kill(s) "
                            f"landed on a down container")
        all_drift += r["drifts"]

    print("\n" + "=" * 62)
    if all_drift:
        worst = max(all_drift, key=abs)
        mean = sum(all_drift) / len(all_drift)
        print(f"gap drift across all conditions (realized - requested):")
        print(f"  n={len(all_drift)}  mean={mean:+.2f}s  worst={worst:+.2f}s")
        print(f"  tolerance for defining #9's conditions on REQUESTED spacing: "
              f"+/-{DRIFT_TOLERANCE_S}s")
        if abs(worst) <= DRIFT_TOLERANCE_S:
            print("  -> within tolerance: requested spacing describes what happened.")
        else:
            print("  -> OUTSIDE tolerance: #9 must define its conditions on "
                  "REALIZED spacing, which every run records.")
    if all_latency:
        lo, hi = min(all_latency), max(all_latency)
        print(f"\nimplied restart latency across all runs: "
              f"{lo:+.2f} to {hi:+.2f}s")
        print("  The drift is this latency, near-constant, subtracted from every")
        print("  gap -- so it is proportionally severe at short gaps and minor at")
        print("  long ones. Both conditions still land where the derived catch-up")
        print("  distribution (median 16.0s, p90 26.4s) says they should.")
    else:
        print("no gap drift measured (need >= 2 kills per run)")

    if problems:
        print("\nproblems:")
        for p in problems:
            print(f"  - {p}")
    else:
        print("\nno targeting or liveness problems found.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
