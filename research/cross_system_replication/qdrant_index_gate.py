"""
Indexing gate for the Qdrant harness (issue #28, method/qdrant-index-gate).

Why this exists: PR #6's `index_recall` null was measured over a corpus that
Qdrant had not HNSW-indexed for the whole baseline and 60-84% of the chaos
window (research/qdrant_optimizer_masking/). Un-indexed segments are searched
by exact scan, so `index_recall` was ~1.0 by construction -- it measured no
graph. This module lets a run refuse to start its baseline clock until every
replica reports the corpus indexed, so the metric interrogates what it is
defined to interrogate.

Two layers, kept apart so the decision is testable without a cluster
(research/qdrant_index_gate/test_index_gate.py), following
qdrant_kill_scheduler's precedent:

  * `gate_decision(history, tol, consecutive)` -- pure. Given the last few
    polls (each a list of per-node dicts from `/collections/{name}`), says
    whether the gate is closed and why not.
  * `wait_for_index_gate(...)` -- polls the cluster, feeds the decision,
    and returns a record for run_meta.json. It never raises on a slow gate;
    it returns closed=False after the timeout and the caller decides what a
    run that could not reach an indexed corpus should do (the harness fails
    the run, so qdrant_sweep.py records FAILED and no samples.csv exists).

`indexed_vectors_count` is Qdrant's own report, not ground truth (SPEC.md,
Confounds). The decision therefore also requires `status == "green"` on
every node, and the poll records `optimizer_status` so a gate that closed
while an optimizer was still mid-rebuild is visible after the fact.
"""
from __future__ import annotations

import time

import qdrant_topology as topo


def poll_index_state(node_ids) -> list[dict]:
    """One poll: each node's own view of the collection. A node that cannot
    be reached contributes a row with `reachable=False`, which the decision
    treats as not-indexed -- an unreachable replica is not a gated one."""
    t = time.time()
    out = []
    for n in node_ids:
        row = {"t": t, "node": n, "reachable": False,
               "indexed_vectors_count": None, "points_count": None,
               "segments_count": None, "status": None, "optimizer_status": None}
        try:
            status, body = topo.http_request(
                topo.http_port(n), "GET", f"/collections/{topo.COLLECTION}",
                timeout=2.0)
            if status == 200:
                res = body.get("result", {})
                row.update({
                    "reachable": True,
                    "indexed_vectors_count": res.get("indexed_vectors_count"),
                    "points_count": res.get("points_count"),
                    "segments_count": res.get("segments_count"),
                    "status": res.get("status"),
                    "optimizer_status": res.get("optimizer_status"),
                })
        except Exception as e:  # noqa: BLE001 -- recorded, not swallowed
            row["error"] = repr(e)
        out.append(row)
    return out


def node_fraction(row: dict) -> float:
    """indexed / points for one node's poll row; 0.0 when it cannot be
    computed (unreachable, no points yet, missing fields). A collection with
    zero points is NOT indexed for gate purposes -- 0/0 must not close a
    gate before the writers have written anything."""
    if not row.get("reachable"):
        return 0.0
    idx = row.get("indexed_vectors_count")
    pts = row.get("points_count")
    if idx is None or not pts:
        return 0.0
    return min(1.0, max(0.0, idx / pts))


def poll_min_fraction(poll: list[dict]) -> float:
    return min((node_fraction(r) for r in poll), default=0.0)


def gate_decision(history: list[list[dict]], tol: float = 0.0,
                  consecutive: int = 3) -> tuple[bool, float, str]:
    """Pure decision over the poll history.

    Closed iff the last `consecutive` polls each have EVERY node reachable,
    `status == "green"`, and indexed/points >= 1 - tol. Returns
    (closed, min_fraction_in_last_poll, reason). `reason` is "" when closed
    and otherwise names the first failing condition, for the log line.
    """
    if consecutive < 1:
        raise ValueError("consecutive must be >= 1")
    if not history:
        return False, 0.0, "no polls yet"
    last = history[-1]
    last_min = poll_min_fraction(last)
    if len(history) < consecutive:
        return False, last_min, f"{len(history)}/{consecutive} polls"
    threshold = 1.0 - tol
    for k, poll in enumerate(history[-consecutive:]):
        if not poll:
            return False, last_min, f"poll -{consecutive - k}: no nodes reported"
        for r in poll:
            n = r.get("node")
            if not r.get("reachable"):
                return False, last_min, f"node {n} unreachable"
            if r.get("status") != "green":
                return False, last_min, f"node {n} status={r.get('status')!r}"
            f = node_fraction(r)
            if f < threshold:
                return False, last_min, (f"node {n} indexed {f:.4f} < {threshold:.4f} "
                                         f"({r.get('indexed_vectors_count')}/"
                                         f"{r.get('points_count')})")
    return True, last_min, ""


def wait_for_index_gate(node_ids, tol: float = 0.0, consecutive: int = 3,
                        timeout_s: float = 600.0, poll_s: float = 1.0,
                        log=print) -> dict:
    """Block until `gate_decision` closes or `timeout_s` elapses.

    Returns a JSON-serialisable record for run_meta.json:
      closed, elapsed_s, polls, tol, consecutive, timeout_s,
      per_node_at_close (node -> {indexed, points, fraction, segments,
      status, optimizer_status}), last_reason, and the last poll verbatim.
    """
    t0 = time.time()
    history: list[list[dict]] = []
    closed, last_min, reason = False, 0.0, "no polls yet"
    last_log = 0.0
    while True:
        history.append(poll_index_state(node_ids))
        if len(history) > max(consecutive, 3):
            history.pop(0)
        closed, last_min, reason = gate_decision(history, tol, consecutive)
        elapsed = time.time() - t0
        if closed:
            log(f"[gate] closed after {elapsed:.1f}s: every replica indexed "
                f">= {1.0 - tol:.4f} for {consecutive} consecutive polls")
            break
        if elapsed >= timeout_s:
            log(f"[gate] NOT closed after {elapsed:.1f}s (timeout {timeout_s:.0f}s): "
                f"{reason}")
            break
        if elapsed - last_log >= 10.0:
            log(f"[gate] {elapsed:5.0f}s  min indexed fraction {last_min:.4f}  ({reason})")
            last_log = elapsed
        time.sleep(poll_s)

    last = history[-1] if history else []
    return {
        "closed": closed,
        "elapsed_s": round(time.time() - t0, 3),
        "polls": len(history) if closed else None,
        "tol": tol,
        "consecutive": consecutive,
        "timeout_s": timeout_s,
        "min_fraction_at_end": round(last_min, 6),
        "last_reason": reason,
        "per_node_at_end": {
            str(r.get("node")): {
                "indexed": r.get("indexed_vectors_count"),
                "points": r.get("points_count"),
                "fraction": round(node_fraction(r), 6),
                "segments": r.get("segments_count"),
                "status": r.get("status"),
                "optimizer_status": r.get("optimizer_status"),
                "reachable": r.get("reachable"),
            } for r in last
        },
    }
