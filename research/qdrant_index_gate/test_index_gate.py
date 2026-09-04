#!/usr/bin/env python3
"""Validation for the indexing gate's decision (issue #28). No cluster needed.

The gate's job is to refuse to start a baseline clock until every replica
reports the corpus indexed, and to refuse LOUDLY rather than quietly when it
cannot. These checks cover the pure decision, `gate_decision`, against
synthetic poll histories, following `qdrant_kill_scheduler/test_kill_schedule.py`'s
precedent that the logic gets tested without standing up Docker. What this
does NOT cover: whether Qdrant's `indexed_vectors_count` means what the gate
assumes (SPEC.md, Confounds) -- that is the live run's job.

Usage:
    python research/qdrant_index_gate/test_index_gate.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "cross_system_replication"))

from qdrant_index_gate import gate_decision, node_fraction  # noqa: E402

checks = []


def check(name, cond, detail=""):
    checks.append((name, bool(cond), detail))
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  -- {detail}" if detail and not cond else ""))


def row(node, indexed, points, status="green", reachable=True):
    return {"node": node, "reachable": reachable, "indexed_vectors_count": indexed,
            "points_count": points, "segments_count": 4, "status": status,
            "optimizer_status": "ok"}


def poll(*fractions, points=100_000, status="green"):
    return [row(i, int(f * points), points, status=status) for i, f in enumerate(fractions)]


print("gate_decision")

closed, frac, why = gate_decision([], 0.0, 3)
check("empty history is not closed", not closed and why == "no polls yet")

h = [poll(1.0, 1.0, 1.0)] * 2
closed, frac, why = gate_decision(h, 0.0, 3)
check("two perfect polls do not satisfy consecutive=3", not closed and why == "2/3 polls", why)

h = [poll(1.0, 1.0, 1.0)] * 3
closed, frac, why = gate_decision(h, 0.0, 3)
check("three perfect polls close at tol=0", closed and frac == 1.0 and why == "")

h = [poll(1.0, 1.0, 1.0), poll(1.0, 0.999, 1.0), poll(1.0, 1.0, 1.0)]
closed, frac, why = gate_decision(h, 0.0, 3)
check("one node one-vector short in the middle poll blocks at tol=0",
      not closed and why.startswith("node 1 indexed 0.9990"), why)

closed, frac, why = gate_decision(h, 0.01, 3)
check("same history closes at tol=0.01", closed)

h = [poll(0.2, 0.3, 0.1)] * 3
closed, frac, why = gate_decision(h, 0.0, 3)
check("min fraction reported is the worst node", not closed and abs(frac - 0.1) < 1e-9, f"frac={frac}")

h = [poll(1.0, 1.0, 1.0)] * 2 + [poll(1.0, 1.0, 1.0, status="yellow")]
closed, frac, why = gate_decision(h, 0.0, 3)
check("status != green blocks even at full index", not closed and "status='yellow'" in why, why)

h = [poll(1.0, 1.0, 1.0)] * 2 + [[row(0, 100, 100), row(1, 100, 100, reachable=False), row(2, 100, 100)]]
closed, frac, why = gate_decision(h, 0.0, 3)
check("unreachable node blocks", not closed and why == "node 1 unreachable", why)

h = [[row(0, 0, 0), row(1, 0, 0), row(2, 0, 0)]] * 3
closed, frac, why = gate_decision(h, 0.0, 3)
check("zero points is NOT indexed (0/0 must not close the gate)", not closed and frac == 0.0, why)

h = [[]] * 3
closed, frac, why = gate_decision(h, 0.0, 3)
check("a poll with no nodes blocks", not closed and "no nodes reported" in why, why)

h = [poll(1.0, 1.0, 1.0)] * 5
closed, frac, why = gate_decision(h, 0.0, 1)
check("consecutive=1 closes on one good poll", closed)

try:
    gate_decision(h, 0.0, 0)
    check("consecutive=0 is refused", False)
except ValueError:
    check("consecutive=0 is refused", True)

# Reported counts exceeding points (seen transiently during optimizer
# rewrites) must clamp, not exceed 1.0 and must not be treated as indexed
# beyond the corpus.
check("indexed > points clamps to 1.0", node_fraction(row(0, 120, 100)) == 1.0)
check("missing indexed field is 0.0", node_fraction({"reachable": True, "points_count": 5}) == 0.0)

print()
n_fail = sum(1 for _, ok, _ in checks if not ok)
print(f"{len(checks) - n_fail}/{len(checks)} checks passed")
print()
print("Not covered here (needs a live cluster, SPEC.md Confounds): whether "
      "indexed_vectors_count == points_count on every node actually means "
      "searches are served by HNSW rather than exact scan.")
sys.exit(1 if n_fail else 0)
