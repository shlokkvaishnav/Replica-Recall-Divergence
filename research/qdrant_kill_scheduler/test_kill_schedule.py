#!/usr/bin/env python3
"""Validation for the controlled kill scheduler (issue #17). No cluster needed.

Issue #17's deliverable is an instrument that provably emits the schedules it
was asked for, and whose failures are visible rather than silent. These checks
cover the two halves of that:

  * `build_kill_schedule` produces the requested spacing and targeting, holds
    the stated invariants constant across conditions, and REFUSES an
    infeasible request instead of quietly compressing gaps.
  * `chaos_loop_scheduled` records realized alongside requested values, and
    flags a kill that landed on an already-down container.

The second is exercised against a fake container rather than Docker, following
`research/replica_recall/test_metrics.py`'s precedent that the measurement core
gets tested without standing up a cluster. What that does NOT cover is stated
in the summary at the bottom, rather than left for a reader to discover.

Usage:
    python research/qdrant_kill_scheduler/test_kill_schedule.py
"""
import os
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "cross_system_replication"))

import qdrant_docker_harness as dh  # noqa: E402

NODES = ["rrd-qdrant-node0", "rrd-qdrant-node1", "rrd-qdrant-node2"]
checks = []


def check(name, cond, detail=""):
    checks.append((name, bool(cond), detail))
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  -- {detail}" if detail and not cond else ""))


class FakeContainer:
    """Stands in for DockerContainer: records calls, tracks liveness."""

    def __init__(self, name):
        self.name = name
        self.restart_count = 0
        self._alive = True
        self.log = []

    def is_alive(self):
        return self._alive

    def kill(self):
        self.log.append(("kill", time.time()))
        self._alive = False

    def start(self):
        self.log.append(("start", time.time()))
        self._alive = True
        self.restart_count += 1
        return True


print("build_kill_schedule -- requested spacing and targeting")

sched = dh.build_kill_schedule("short-gap-same-node", NODES, 3, 300.0)
check("short-gap: all kills hit one node", len({s["target"] for s in sched}) == 1)
check("short-gap: uses the derived SHORT_GAP_S",
      all(s["gap_s"] == dh.SHORT_GAP_S for s in sched[1:]),
      f"got {[s['gap_s'] for s in sched[1:]]}")
check("short-gap: first kill has no gap", sched[0]["gap_s"] is None)
gaps = [round(sched[i]["at_s"] - sched[i - 1]["at_s"], 3) for i in range(1, len(sched))]
check("short-gap: offsets encode gap + downtime",
      all(abs(g - (dh.SHORT_GAP_S + dh.FIXED_DOWN_S)) < 1e-6 for g in gaps),
      f"offsets {gaps}")

long_s = dh.build_kill_schedule("long-gap-same-node", NODES, 3, 300.0)
check("long-gap: uses the derived LONG_GAP_S",
      all(s["gap_s"] == dh.LONG_GAP_S for s in long_s[1:]))
check("long-gap is above the derived catch-up p90 (26.4s), short is below "
      "the median (16.0s)",
      dh.LONG_GAP_S > 26.4 and dh.SHORT_GAP_S < 16.0,
      f"SHORT={dh.SHORT_GAP_S} LONG={dh.LONG_GAP_S}")

spread = dh.build_kill_schedule("spread", NODES, 3, 300.0)
check("spread: every kill hits a different node",
      len({s["target"] for s in spread}) == 3)

print("\nbuild_kill_schedule -- invariants held constant across conditions")
check("kill count identical across conditions",
      len(sched) == len(long_s) == len(spread) == 3)
check("down-time fixed and identical across conditions",
      {s["down_for_s"] for s in sched + long_s + spread} == {dh.FIXED_DOWN_S})

print("\nbuild_kill_schedule -- refuses rather than silently compressing")


def raises(fn, needle):
    try:
        fn()
    except ValueError as e:
        return needle.lower() in str(e).lower()
    return False


check("infeasible window raises, naming the window",
      raises(lambda: dh.build_kill_schedule("long-gap-same-node", NODES, 3, 30.0),
             "chaos window"))
check("infeasible window does NOT shorten the gap (the independent variable)",
      raises(lambda: dh.build_kill_schedule("long-gap-same-node", NODES, 3, 30.0),
             "do not shorten the gap"))
check("spread with more kills than nodes raises",
      raises(lambda: dh.build_kill_schedule("spread", NODES, 5, 300.0), "distinct node"))
check("unknown condition raises", raises(
    lambda: dh.build_kill_schedule("no-such-condition", NODES, 3, 300.0), "unknown condition"))
check("n_kills < 2 raises", raises(
    lambda: dh.build_kill_schedule("spread", NODES, 1, 300.0), "n_kills"))

print("\nchaos_loop_scheduled -- realized values recorded, not assumed")

containers = {n: FakeContainer(n) for n in NODES}
events = []
fast = [
    {"seq": 0, "target": NODES[0], "at_s": 0.0, "gap_s": None, "down_for_s": 0.05},
    {"seq": 1, "target": NODES[0], "at_s": 0.30, "gap_s": 0.2, "down_for_s": 0.05},
]
dh.chaos_loop_scheduled(threading.Event(), containers, events, fast, "test-condition")

check("one event per scheduled kill", len(events) == 2, f"got {len(events)}")
check("condition recorded on every event",
      all(e["condition"] == "test-condition" for e in events))
check("requested gap preserved", events[1]["requested_gap_s"] == 0.2)
check("realized gap measured, not copied from the request",
      events[1]["realized_gap_s"] is not None
      and events[1]["realized_gap_s"] != events[1]["requested_gap_s"],
      f"realized={events[1]['realized_gap_s']}")
check("realized gap is close to requested for a healthy node",
      abs(events[1]["realized_gap_s"] - 0.25) < 0.15,
      f"realized={events[1]['realized_gap_s']}")
check("first kill has no realized gap", events[0]["realized_gap_s"] is None)
check("killed_while_down false when the node was up",
      all(e["killed_while_down"] is False for e in events))
check("existing event fields kept, so old analysis tools still parse",
      all({"t", "target", "alive_after_restart", "down_for_s",
           "restart_count"} <= set(e) for e in events))

print("\nchaos_loop_scheduled -- a kill landing on a down node is flagged")
containers2 = {n: FakeContainer(n) for n in NODES}
containers2[NODES[0]]._alive = False
events2 = []
dh.chaos_loop_scheduled(threading.Event(), containers2, events2,
                        [{"seq": 0, "target": NODES[0], "at_s": 0.0,
                          "gap_s": None, "down_for_s": 0.05}], "test-condition")
check("killed_while_down true when the container was already down",
      events2[0]["killed_while_down"] is True)

print("\nstop event halts the schedule early")
containers3 = {n: FakeContainer(n) for n in NODES}
events3, ev = [], threading.Event()
ev.set()
dh.chaos_loop_scheduled(ev, containers3, events3,
                        [{"seq": 0, "target": NODES[0], "at_s": 5.0,
                          "gap_s": None, "down_for_s": 0.05}], "test-condition")
check("no kills executed after stop is set", events3 == [])

passed = sum(1 for _, ok, _ in checks if ok)
print(f"\n{passed}/{len(checks)} checks passed")
print("""
NOT covered by these checks, stated rather than implied:
  * that a real Qdrant container restarts within FIXED_DOWN_S -- FakeContainer
    always comes back instantly, so realized-vs-requested drift under a slow
    real restart (issue #17's expected outcome (b)) can only be measured on a
    live cluster.
  * that SHORT_GAP_S/LONG_GAP_S separate healing outcomes. That is #9's
    question, not this instrument's.
""")
sys.exit(0 if passed == len(checks) else 1)
