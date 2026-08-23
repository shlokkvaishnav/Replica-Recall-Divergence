"""
Docker-container chaos harness for the Qdrant cluster, analogous to
chaos_harness.py.

Documented fault-model difference (SPEC.md's Confounds section calls this
out explicitly): nano-db's chaos_harness SIGKILLs a bare process and
restarts it as a fresh `subprocess.Popen`. This harness instead does
`docker kill` (SIGKILL to the container's PID 1) followed by `docker
start` on the same container, so the on-disk volume persists across the
kill exactly like nano-db's on-disk shard state does -- but the container
runtime's own supervision, and Qdrant's own WAL replay on startup, add
behavior nano-db's bare process restart does not have. This is not
papered over: it is why SPEC.md treats "matched, not identical" as the
standard for this branch's fault model rather than claiming equivalence.
"""

from __future__ import annotations

import random
import subprocess
import threading
import time

import qdrant_topology as topo


class ManagedContainer:
    """Wraps one named Docker container with kill/restart, mirroring
    chaos_harness.ManagedProcess's interface so the chaos/validator loop
    shapes stay recognizably the same."""

    def __init__(self, name: str):
        self.name = name
        self.restart_count = 0
        self.crash_on_restart_count = 0

    def _docker(self, *args, timeout=30) -> subprocess.CompletedProcess:
        return subprocess.run(["docker", *args], capture_output=True,
                              text=True, timeout=timeout)

    def is_alive(self) -> bool:
        r = self._docker("inspect", "-f", "{{.State.Running}}", self.name)
        return r.returncode == 0 and r.stdout.strip() == "true"

    def kill(self) -> None:
        self._docker("kill", self.name)

    def start(self, grace_s: float = 2.0) -> bool:
        self._docker("start", self.name)
        time.sleep(grace_s)
        return self.is_alive()

    def restart(self) -> bool:
        self.kill()
        self.restart_count += 1
        alive = self.start()
        if not alive:
            self.crash_on_restart_count += 1
        return alive


def build_containers(node_ids) -> dict[str, ManagedContainer]:
    return {topo.container_name(n): ManagedContainer(topo.container_name(n))
            for n in node_ids}


class Violation:
    def __init__(self, kind: str, detail: str):
        self.kind = kind
        self.detail = detail
        self.t = time.time()

    def __repr__(self):
        return f"[{self.kind}] {self.detail}"


def validator_loop(stop_evt, node_ids, violations: list, checks_run: list):
    """Watches for the Qdrant analog of chaos_harness's DOUBLE_PRIMARY
    check. Qdrant has no primary/secondary distinction to violate (see
    qdrant_topology.py's docstring), so the invariant this checks instead
    is Raft-level: no two nodes simultaneously claim `role: Leader` with
    conflicting terms, which would indicate a split-brain rather than an
    ordinary leader handoff (a single term transition is expected and not
    a violation)."""
    last_leader_term: dict[int, int] = {}
    while not stop_evt.is_set():
        leaders_this_round: dict[int, int] = {}  # node -> term
        for n in node_ids:
            try:
                status, body = topo.http_request(
                    topo.http_port(n), "GET", "/cluster", timeout=1.0)
            except Exception:
                continue
            if status != 200:
                continue
            checks_run[0] += 1
            raft = body.get("result", {}).get("raft_info", {})
            if raft.get("role") == "Leader":
                leaders_this_round[n] = raft.get("term")
        terms = set(leaders_this_round.values())
        if len(leaders_this_round) > 1 and len(terms) == 1:
            violations.append(Violation(
                "SPLIT_BRAIN",
                f"multiple nodes claim Leader at the same term "
                f"{terms}: {leaders_this_round}"))
        last_leader_term.update(leaders_this_round)
        stop_evt.wait(1.0)


def chaos_loop(stop_evt, containers: dict, events: list,
              min_interval=3.0, max_interval=6.0, min_down=1.0, max_down=8.0):
    """Same shape as chaos_harness.chaos_loop -- kill one target, hold it
    down for a randomized window, restart. Down-time range widened versus
    nano-db's (0.5-5s) because Qdrant's own container start + WAL replay
    is slower than a bare process fork/exec; a too-short window would
    mostly measure "restarting", not "recovered", every time."""
    names = list(containers.keys())
    while not stop_evt.is_set():
        time.sleep(random.uniform(min_interval, max_interval))
        if stop_evt.is_set():
            break
        target = random.choice(names)
        c = containers[target]
        t0 = time.time()
        c.kill()
        down_for = random.uniform(min_down, max_down)
        time.sleep(down_for)
        if stop_evt.is_set():
            c.start()
            break
        alive = c.start()
        events.append({
            "t": t0, "target": target, "alive_after_restart": alive,
            "down_for_s": down_for, "restart_count": c.restart_count,
        })
