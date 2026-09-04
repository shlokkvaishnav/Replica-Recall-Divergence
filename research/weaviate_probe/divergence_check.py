#!/usr/bin/env python3
"""
Can the isolation probe SEE a divergence that was deliberately created?
(Issue #41, the decisive half of path (a).)

`feasibility_check.py` shows an isolated node answers, and answers the same as
it did in a healthy cluster. That is necessary and not sufficient: a probe
that returns the coordinator's merged view, or a cached one, would pass both
checks. This creates a known divergence and asks whether the probe reports it.

Protocol:

  1. baseline: every replica holds N objects (all three up).
  2. stop node2. Write M more objects at consistency ONE -- they land on
     node0/node1 and NOT on node2.
  3. start node2, then IMMEDIATELY stop node0 and node1. With no peers up,
     Weaviate's async hash-tree repair has nothing to sync from, so node2
     stays diverged for as long as it is alone -- which is what makes the
     divergence observable rather than a race against the repair.
  4. query node2 alone at consistency ONE. It should report N, not N+M.
  5. restart the peers, wait, and confirm async replication converges it to
     N+M -- which both cleans up and demonstrates the anti-entropy this whole
     Weaviate leg exists to test.

A probe that reports N+M at step 4 is reading something other than node2's own
state, and path (a) is invalid however convenient it is.

Usage (cluster up, class created, N objects written):
    python research/weaviate_probe/divergence_check.py [--extra 100]
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import uuid

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import weaviate_topology as t  # noqa: E402
from feasibility_check import docker, object_ids, search  # noqa: E402


def log(m):
    print(m, flush=True)


def count_via_list(node: int) -> int | None:
    ok, ids = object_ids(node)
    return len(ids) if ok else None


def wait_ready(node: int, timeout_s: float = 90) -> bool:
    end = time.time() + timeout_s
    while time.time() < end:
        st, _ = t.http_request(t.http_port(node), "GET", "/v1/.well-known/ready", timeout=3)
        if st == 200:
            return True
        time.sleep(2)
    return False


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--extra", type=int, default=100)
    ap.add_argument("--start-id", type=int, default=100000)
    a = ap.parse_args()

    victim, peers = 2, [0, 1]

    n_before = count_via_list(0)
    log(f"1. baseline: node0 lists {n_before} objects")

    log(f"2. stopping node{victim}, then writing {a.extra} objects at consistency ONE")
    docker("stop", t.container_name(victim))
    time.sleep(5)
    rng = np.random.default_rng(4242)
    objs = [{"class": t.CLASS_NAME, "id": str(uuid.UUID(int=a.start_id + i)),
             "properties": {"vid": f"x{i}"},
             "vector": [float(x) for x in rng.standard_normal(t.VECTOR_DIM)]}
            for i in range(a.extra)]
    st, body = t.http_request(t.http_port(0), "POST",
                              "/v1/batch/objects?consistency_level=ONE",
                              {"objects": objs}, timeout=120)
    errs = [o for o in (body if isinstance(body, list) else [])
            if (o.get("result") or {}).get("errors")]
    log(f"   batch -> {st}, {len(body) if isinstance(body, list) else '?'} objects, {len(errs)} errors")
    if errs:
        log("   " + json.dumps(errs[0])[:300])

    n_peer = count_via_list(0)
    log(f"   node0 now lists {n_peer}")

    log(f"3. starting node{victim}, then immediately stopping peers {peers} "
        f"(no peer => async repair has nothing to sync from)")
    docker("start", t.container_name(victim))
    for p in peers:
        docker("stop", t.container_name(p))
    ready = wait_ready(victim)
    log(f"   node{victim} ready alone: {ready}")

    log(f"4. probing node{victim} alone")
    n_victim = count_via_list(victim)
    log(f"   node{victim} lists {n_victim} objects "
        f"(diverged baseline {n_before}, peers hold {n_peer})")
    st_nodes, nodes = t.nodes_status(victim, verbose=True)
    for n in (nodes.get("nodes") or []):
        for s in (n.get("shards") or []):
            log(f"   /v1/nodes says {n['name']} shard {s.get('name')}: "
                f"objectCount={s.get('objectCount')} status={n.get('status')}")

    sees = (n_victim is not None and n_before is not None and n_peer is not None
            and n_victim == n_before and n_peer > n_before)
    if n_victim is None:
        verdict = "INCONCLUSIVE (isolated victim would not serve a list)"
    elif sees:
        verdict = (f"SEES DIVERGENCE ({n_victim} on the victim vs {n_peer} on its peers) "
                   f"-- the probe reports this replica's own state")
    elif n_victim == n_peer:
        verdict = (f"BLIND ({n_victim} == peers) -- the probe is not reporting this "
                   f"replica's own state; path (a) is invalid")
    else:
        verdict = f"UNEXPECTED (victim {n_victim}, before {n_before}, peers {n_peer})"
    log(f"   VERDICT: {verdict}")

    log("5. restarting peers and watching async replication converge the victim")
    for p in peers:
        docker("start", t.container_name(p))
    for p in peers:
        wait_ready(p)
    conv, t0 = None, time.time()
    while time.time() - t0 < 180:
        c = count_via_list(victim)
        if c == n_peer:
            conv = round(time.time() - t0, 1)
            break
        time.sleep(5)
    log(f"   victim converged to {n_peer}: "
        f"{'yes, after ~' + str(conv) + 's (bounded by a 5s poll)' if conv is not None else 'NOT within 180s'}")
    log("\nsummary: " + verdict)
    return 0


if __name__ == "__main__":
    sys.exit(main())
