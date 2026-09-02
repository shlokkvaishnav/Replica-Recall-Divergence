#!/usr/bin/env python3
"""Derive Qdrant's post-kill catch-up time from already-collected sweep data.

Issue #17 requires the short/long same-node-gap boundary to be a number read
off observation, not a guess: "the 'typical catch-up time' that separates short
from long must be derived from observation and stated as a number in the spec,
not assumed."

This reads `research/cross_system_replication/results_sweep/seed*_chaos/`
(committed by PR #6, no new runs) and asks, per kill: once that node came back,
how long until the replicas it hosts were level with their peers again?

Definition, and why this one
---------------------------
"Caught up" is deliberately *relative to peers at the same instant*, not
absolute completeness. Writes keep flowing during a run, so a killed node's
completeness ratio can climb while it is still behind, and can sit below 1.0
forever without being behind at all -- the same "dilution trap" DECISION_LOG.md
records for the healing metric. Peer-relative comparison is immune to both.

For a kill of node n at t_k:
  * consider every later sample of a replica hosted on n (replica_id == n --
    see qdrant_topology.py: one Qdrant node per replica slot, so a sample named
    shard-{s}-{r} lives on node r);
  * at each sample round, compare that replica's completeness against the best
    completeness among the *other* replicas of the same shard in that same
    round;
  * the node is "caught up" at the first round where every shard it hosts is
    within EPS of its best peer;
  * observation stops at the next kill of the same node (censored), because
    after that the node is recovering from a different event.

Censored observations are reported, never silently dropped or counted as
instant catch-up -- a kill whose recovery was interrupted is exactly the case
the short-gap condition is built to create, so miscounting it would bias the
number that defines that condition.

Usage:
    python research/qdrant_kill_scheduler/derive_catchup_time.py \
        [--sweep-dir research/cross_system_replication/results_sweep] [--eps 0.005]
"""
import argparse
import csv
import glob
import json
import os
import re
import statistics
from collections import defaultdict

EPS_DEFAULT = 0.005


def load_run(run_dir):
    events = json.load(open(os.path.join(run_dir, "events.json")))
    meta = json.load(open(os.path.join(run_dir, "run_meta.json")))
    samples = list(csv.DictReader(open(os.path.join(run_dir, "samples.csv"))))
    t0 = meta.get("t_start") or meta.get("start_time")
    kills = []
    for e in events:
        node = int(re.search(r"node(\d+)", e["target"]).group(1))
        t_rel = e.get("t_rel")
        if t_rel is None and t0 is not None:
            t_rel = e["t"] - t0
        kills.append({"node": node, "t_rel": t_rel,
                      "down_for_s": e.get("down_for_s"),
                      "alive": e.get("alive_after_restart")})
    kills.sort(key=lambda k: k["t_rel"])
    return kills, samples


def rounds_by_time(samples):
    """Group sample rows into probe rounds keyed by t_rel."""
    rounds = defaultdict(list)
    for r in samples:
        if r.get("reachable") != "1" or not r.get("completeness"):
            continue
        rounds[float(r["t_rel"])].append(
            {"shard": int(r["shard"]), "replica": int(r["replica"]),
             "completeness": float(r["completeness"])})
    return sorted(rounds.items())


def catchup_for_kill(kill, next_same_node_kill_t, rounds, eps):
    """Seconds from kill until node's shards are all within eps of best peer.

    Returns (seconds, 'ok') or (observed_window, 'censored').
    """
    t_k = kill["t_rel"]
    node = kill["node"]
    last_seen = t_k
    for t, rows in rounds:
        if t <= t_k:
            continue
        if next_same_node_kill_t is not None and t >= next_same_node_kill_t:
            return (last_seen - t_k, "censored")
        last_seen = t
        mine = [r for r in rows if r["replica"] == node]
        if not mine:
            continue
        level = True
        for r in mine:
            peers = [p["completeness"] for p in rows
                     if p["shard"] == r["shard"] and p["replica"] != node]
            if not peers:
                continue
            if r["completeness"] < max(peers) - eps:
                level = False
                break
        if level:
            return (t - t_k, "ok")
    return (last_seen - t_k, "censored")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sweep-dir",
                    default="research/cross_system_replication/results_sweep")
    ap.add_argument("--eps", type=float, default=EPS_DEFAULT,
                    help="completeness tolerance for 'level with peers'")
    args = ap.parse_args()

    ok, censored, per_run = [], [], []
    for run_dir in sorted(glob.glob(os.path.join(args.sweep_dir, "seed*_chaos"))):
        kills, samples = load_run(run_dir)
        rounds = rounds_by_time(samples)
        run_ok = []
        for i, k in enumerate(kills):
            nxt = next((x["t_rel"] for x in kills[i + 1:] if x["node"] == k["node"]),
                       None)
            secs, status = catchup_for_kill(k, nxt, rounds, args.eps)
            (ok if status == "ok" else censored).append(secs)
            if status == "ok":
                run_ok.append(secs)
        per_run.append((os.path.basename(run_dir), len(kills), run_ok))

    print(f"sweep-dir : {args.sweep_dir}")
    print(f"eps       : {args.eps} completeness, peer-relative\n")
    print(f"{'run':<28}{'kills':>6}{'measured':>10}{'median s':>10}")
    for name, nk, runs in per_run:
        med = f"{statistics.median(runs):.1f}" if runs else "--"
        print(f"{name:<28}{nk:>6}{len(runs):>10}{med:>10}")

    print(f"\nmeasured catch-ups : {len(ok)}")
    print(f"censored (next kill to the same node landed first) : {len(censored)}")
    if ok:
        ok_s = sorted(ok)
        print(f"  min / median / max : {ok_s[0]:.1f} / "
              f"{statistics.median(ok_s):.1f} / {ok_s[-1]:.1f} s")
        print(f"  mean +/- sd        : {statistics.mean(ok_s):.1f} +/- "
              f"{statistics.pstdev(ok_s):.1f} s")
        p90 = ok_s[max(0, int(round(0.9 * len(ok_s))) - 1)]
        print(f"  p90                : {p90:.1f} s")
    if censored:
        print(f"  censored windows observed up to : "
              f"{max(censored):.1f} s (these are lower bounds, not catch-ups)")


if __name__ == "__main__":
    main()
