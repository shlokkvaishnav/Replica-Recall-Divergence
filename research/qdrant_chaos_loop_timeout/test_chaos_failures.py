#!/usr/bin/env python3
"""Validation for the chaos loops' failure handling (issue #38). No Docker.

The defect: `ManagedContainer._docker` runs `subprocess.run(..., timeout=30)`,
so a hung Docker daemon raises `TimeoutExpired` -- and both chaos loops run in
daemon threads, where an uncaught exception ends the loop silently. The run
then completes normally with an empty `events.json`, indistinguishable from a
healthy run except by noticing there were no kills. It happened once
(`research/qdrant_index_recall_healing/results/seed20261100_quiesce/`, a 127s
window with zero events) and cost one of five pre-registered seeds.

These checks exercise both loops against fake containers that raise, following
`../qdrant_kill_scheduler/test_kill_schedule.py`'s precedent of testing the
chaos machinery without standing up a cluster. What this does NOT cover is
stated at the bottom.

Usage:
    python research/qdrant_chaos_loop_timeout/test_chaos_failures.py
"""
import os
import subprocess
import sys
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "cross_system_replication"))

import qdrant_docker_harness as dh  # noqa: E402

checks = []


def check(name, cond, detail=""):
    checks.append((name, bool(cond), detail))
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  -- {detail}" if detail and not cond else ""))


class FakeContainer:
    """kill() raises on the first `fail_times` calls, then behaves."""

    def __init__(self, name, fail_times=0, exc=None):
        self.name = name
        self.restart_count = 0
        self.kills = 0
        self._fail_times = fail_times
        self._exc = exc or subprocess.TimeoutExpired(cmd=["docker", "kill", name], timeout=30)

    def is_alive(self):
        return True

    def kill(self):
        self.kills += 1
        if self.kills <= self._fail_times:
            raise self._exc

    def start(self, grace_s=0.0):
        self.restart_count += 1
        return True


print("chaos_loop (randomized)")

# A kill that raises must produce a FAILED EVENT, not silence, and the loop
# must still be running afterwards.
c = FakeContainer("rrd-qdrant-node0", fail_times=1)
events = []
stop = threading.Event()
t = threading.Thread(target=dh.chaos_loop,
                     args=(stop, {"rrd-qdrant-node0": c}, events),
                     kwargs=dict(min_interval=0.01, max_interval=0.02,
                                 min_down=0.01, max_down=0.02), daemon=True)
t.start()
threading.Event().wait(0.6)
stop.set()
t.join(timeout=3)

check("a raising kill produces an event rather than silence", len(events) >= 1,
      f"events={len(events)}")
if events:
    e0 = events[0]
    check("the failed event is marked failed", e0.get("failed") is True, str(e0))
    check("the failed event records the exception", "TimeoutExpired" in (e0.get("error") or ""), str(e0.get("error")))
    check("timed_out is true for a TimeoutExpired", e0.get("timed_out") is True, str(e0))
    check("alive_after_restart is None, not False", e0.get("alive_after_restart") is None, str(e0))
check("the loop survives the failure and keeps killing", len(events) >= 2,
      f"events={len(events)} (expected the retry after the failed one)")
check("a later successful event is not marked failed",
      any(e.get("failed") is False for e in events), f"{events[:3]}")
check("the thread is not left running", not t.is_alive())

# A non-timeout exception is recorded too, with timed_out false.
c2 = FakeContainer("rrd-qdrant-node1", fail_times=1, exc=RuntimeError("docker daemon gone"))
ev2 = []
stop2 = threading.Event()
t2 = threading.Thread(target=dh.chaos_loop,
                      args=(stop2, {"rrd-qdrant-node1": c2}, ev2),
                      kwargs=dict(min_interval=0.01, max_interval=0.02,
                                  min_down=0.01, max_down=0.02), daemon=True)
t2.start()
threading.Event().wait(0.4)
stop2.set()
t2.join(timeout=3)
check("a non-timeout exception is recorded with timed_out false",
      bool(ev2) and ev2[0].get("failed") is True and ev2[0].get("timed_out") is False,
      str(ev2[:1]))

print()
print("chaos_loop_scheduled (controlled)")

names = ["rrd-qdrant-node0", "rrd-qdrant-node1", "rrd-qdrant-node2"]
sched = dh.build_kill_schedule("short-gap-same-node", names, 3, 60.0,
                               target_node="rrd-qdrant-node0")
# Compress the schedule so the test runs in seconds, not the window's minute.
for i, step in enumerate(sched):
    step["at_s"] = i * 0.05
    step["down_for_s"] = 0.02
cs = {n: FakeContainer(n, fail_times=(1 if n == "rrd-qdrant-node0" else 0)) for n in names}
ev3 = []
stop3 = threading.Event()
t3 = threading.Thread(target=dh.chaos_loop_scheduled,
                      args=(stop3, cs, ev3, sched, "short-gap-same-node"), daemon=True)
t3.start()
t3.join(timeout=10)

check("scheduled loop records the failed step", any(e.get("failed") for e in ev3), str(ev3[:1]))
check("scheduled loop does not stop at the failure",
      sum(1 for e in ev3 if not e.get("failed")) >= 1,
      f"ok={sum(1 for e in ev3 if not e.get('failed'))} of {len(ev3)}")
check("a failed scheduled step keeps its provenance fields",
      all(k in (next((e for e in ev3 if e.get("failed")), {})) for k in
          ("condition", "seq", "requested_at_s", "realized_at_s")),
      str(next((e for e in ev3 if e.get("failed")), {})))
check("scheduled thread finished", not t3.is_alive())

print()
n_fail = sum(1 for _, ok, _ in checks if not ok)
print(f"{len(checks) - n_fail}/{len(checks)} checks passed")
print()
print("Not covered here (needs a live cluster): that a real hung Docker daemon "
      "raises TimeoutExpired at the 30s subprocess timeout rather than hanging "
      "forever, and that run_meta's chaos_no_kills/kill_count/chaos_realized_s "
      "are written from a real run -- the latter is checked by a live smoke run "
      "recorded in SPEC.md.")
sys.exit(1 if n_fail else 0)
