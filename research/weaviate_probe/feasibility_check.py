#!/usr/bin/env python3
"""
Feasibility check for a per-replica Weaviate probe (issue #41).

Runs #41's three paths against a live 3-node cluster and prints a verdict per
path. This is NOT a harness and produces no measurement: it answers only
"can what this project measures be measured on Weaviate at all", which #41
says is the deliverable.

  path (a)  isolation probing -- stop the other replicas, query the survivor
            at consistency ONE, and check the answer is (i) served, (ii)
            served from that node's own state, (iii) able to show a
            divergence that was deliberately created.
  path (b)  GET /v1/nodes?output=verbose per-shard objectCount.
  path (c)  offline per-node data directory (only if a and b fail).

The control #41's Baselines section requires runs first: with all three nodes
up, the same query against each node must return the same ids, or isolation
is changing the answer rather than revealing it.

Usage (cluster already up via weaviate_topology.write_compose_file + compose up):
    python research/weaviate_probe/feasibility_check.py [--n 300]
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


def log(msg):
    print(msg, flush=True)


def search(node: int, vec, k: int, consistency: str = "ONE"):
    """Vector search against one node's HTTP endpoint. Returns (ok, [ids])."""
    q = {
        "query": """{ Get { %s(nearVector: {vector: %s}, limit: %d) { vid _additional { id } } } }"""
                 % (t.CLASS_NAME, json.dumps([float(x) for x in vec]), k)
    }
    st, body = t.http_request(t.http_port(node), "POST", "/v1/graphql", q, timeout=30)
    if st != 200 or not isinstance(body, dict):
        return False, []
    if body.get("errors"):
        return False, body["errors"]
    got = (body.get("data") or {}).get("Get", {}).get(t.CLASS_NAME) or []
    return True, [o.get("vid") for o in got]


def object_ids(node: int, limit: int = 10000, consistency: str = "ONE"):
    """What this node will serve: ids via REST list at a given consistency."""
    st, body = t.http_request(
        t.http_port(node), "GET",
        f"/v1/objects?class={t.CLASS_NAME}&limit={limit}&consistency_level={consistency}",
        timeout=30)
    if st != 200 or not isinstance(body, dict):
        return False, []
    return True, sorted(o.get("id") for o in body.get("objects", []))


def docker(*args, timeout=60):
    return subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--n", type=int, default=300)
    ap.add_argument("--k", type=int, default=10)
    a = ap.parse_args()

    rng = np.random.default_rng(20261300)
    vecs = rng.standard_normal((a.n, t.VECTOR_DIM)).astype("float32")
    probe_vec = rng.standard_normal(t.VECTOR_DIM).astype("float32")
    verdicts = {}

    log("== control: all nodes up, same query to each ==")
    per_node = {}
    for n in range(t.N_NODES):
        ok, ids = search(n, probe_vec, a.k)
        per_node[n] = ids if ok else None
        log(f"  node{n}: {'ok' if ok else 'FAILED'} top-{a.k} = {ids[:4] if ok else ids}")
    healthy_agree = (all(v is not None for v in per_node.values())
                     and len({tuple(v) for v in per_node.values()}) == 1)
    log(f"  control: all three nodes agree on a healthy cluster: "
        f"{'YES' if healthy_agree else 'NO -- any later difference is not attributable to isolation'}")

    log("\n== path (b): /v1/nodes?output=verbose per-shard objectCount ==")
    st, nodes = t.nodes_status(0, verbose=True)
    rows = []
    for n in nodes.get("nodes", []):
        for s in (n.get("shards") or []):
            rows.append((n["name"], s.get("name"), s.get("objectCount"), s.get("vectorIndexingStatus")))
    for r in rows:
        log(f"  {r[0]} shard {r[1]}: objectCount={r[2]} status={r[3]}")
    st2, agg = t.http_request(t.http_port(0), "POST", "/v1/graphql",
                              {"query": "{ Aggregate { %s { meta { count } } } }" % t.CLASS_NAME})
    true_count = (((agg.get("data") or {}).get("Aggregate") or {}).get(t.CLASS_NAME) or [{}])[0] \
        .get("meta", {}).get("count")
    log(f"  Aggregate meta.count = {true_count} (ground truth for the same data)")
    counts = [r[2] for r in rows]
    verdicts["b"] = ("WORKS" if counts and all(c == true_count for c in counts)
                     else f"BROKEN (reports {counts}, actual {true_count})")
    log(f"  verdict (b): {verdicts['b']}")

    log("\n== path (a): isolation probing ==")
    others = [n for n in range(1, t.N_NODES)]
    log(f"  stopping node{others} so node0 must answer from its own state")
    for n in others:
        docker("stop", t.container_name(n))
    time.sleep(8)
    ok_iso, ids_iso = search(0, probe_vec, a.k)
    log(f"  node0 @ONE with peers down: {'served' if ok_iso else 'REFUSED/ERROR'} -> "
        f"{ids_iso[:4] if ok_iso else ids_iso}")
    ok_list, listed = object_ids(0)
    log(f"  node0 object list @ONE with peers down: "
        f"{'served, ' + str(len(listed)) + ' ids' if ok_list else 'REFUSED'}")

    same_as_healthy = ok_iso and per_node.get(0) is not None and list(ids_iso) == list(per_node[0])
    log(f"  isolated answer identical to that node's healthy answer: "
        f"{'YES (isolation did not change the measurement)' if same_as_healthy else 'NO'}")

    for n in others:
        docker("start", t.container_name(n))
    log("  peers restarted")
    time.sleep(20)

    if not ok_iso:
        verdicts["a"] = "DOES NOT WORK (isolated node refused the query)"
    elif not same_as_healthy:
        verdicts["a"] = "INVALID (isolation changed the answer; control fails)"
    else:
        verdicts["a"] = "WORKS (served, and unchanged by isolation) -- divergence-visibility still untested"
    log(f"  verdict (a): {verdicts['a']}")

    log("\n== summary ==")
    for p in ("a", "b"):
        log(f"  path ({p}): {verdicts.get(p, 'not run')}")
    log("  path (c): not run (only if a and b both fail -- see SPEC.md)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
