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


# --- controlled kill scheduling (issue #17) -------------------------------
#
# The default chaos_loop below randomizes timing and targeting, which is
# correct for "does chaos cause divergence" but cannot answer "does kill
# SPACING cause it" -- with a random schedule, spacing is read out of the
# event log after the fact rather than set, which is exactly why PR #6's
# healing-variance analysis could narrow its mechanism but not confirm it.
#
# These conditions make spacing and targeting independent variables. The
# boundary between "short" and "long" is derived, not guessed: see
# research/qdrant_kill_scheduler/derive_catchup_time.py, which measures
# post-kill catch-up from PR #6's own committed sweep data as median 16.0s
# (p90 26.4s, mean 19.3 +/- 13.8, n=25 measured of 42 kills). SHORT_GAP_S
# sits well below that median, LONG_GAP_S comfortably above the p90.
SHORT_GAP_S = 5.0
LONG_GAP_S = 40.0

# Down-time is FIXED across controlled conditions rather than randomized as
# in chaos_loop. If it varied, a condition that happened to draw longer
# outages would differ from its comparison in two ways at once, which is the
# confound this whole scheduler exists to remove. The value is the rough
# midpoint of chaos_loop's own 1-8s range.
FIXED_DOWN_S = 4.5

KILL_CONDITIONS = ("short-gap-same-node", "long-gap-same-node", "spread")


def build_kill_schedule(condition: str, node_names: list, n_kills: int,
                        window_s: float, target_node: str | None = None) -> list:
    """Return the requested schedule as a list of dicts, without running it.

    Each entry: {seq, target, at_s (offset from chaos start), gap_s
    (requested gap since that node's previous restart, None for its first
    kill), down_for_s}.

    Separated from execution on purpose: a schedule can then be inspected,
    tested and diffed against what actually happened, with no cluster
    involved. Raises ValueError when the request does not fit the window,
    rather than silently compressing gaps -- a schedule that quietly stopped
    honouring its own spacing would reintroduce the confound while still
    reporting the condition's name.
    """
    if condition not in KILL_CONDITIONS:
        raise ValueError(f"unknown condition {condition!r}; "
                         f"expected one of {KILL_CONDITIONS}")
    if n_kills < 2:
        raise ValueError("n_kills must be >= 2 for spacing to mean anything")

    if condition == "spread":
        if n_kills > len(node_names):
            raise ValueError(
                f"'spread' needs a distinct node per kill: n_kills={n_kills} "
                f"> {len(node_names)} nodes. Reduce n_kills or use a "
                f"same-node condition.")
        # Spread evenly across the window so total elapsed chaos matches the
        # same-node conditions as closely as the window allows.
        gap = LONG_GAP_S
        targets = list(node_names[:n_kills])
    else:
        gap = SHORT_GAP_S if condition == "short-gap-same-node" else LONG_GAP_S
        chosen = target_node or node_names[0]
        if chosen not in node_names:
            # Same reason the window check raises here rather than at run time:
            # a typo'd node name would otherwise build a plausible-looking
            # schedule, print it, and only fail on containers[name] after the
            # cluster is up, the corpus written and the pre-chaos window spent.
            raise ValueError(
                f"--kill-target-node {chosen!r} is not one of this cluster's "
                f"nodes: {node_names}")
        targets = [chosen] * n_kills

    schedule, at = [], 0.0
    for i in range(n_kills):
        if i > 0:
            at += FIXED_DOWN_S + gap
        schedule.append({
            "seq": i,
            "target": targets[i],
            "at_s": round(at, 3),
            "gap_s": None if i == 0 else gap,
            "down_for_s": FIXED_DOWN_S,
        })

    span = schedule[-1]["at_s"] + FIXED_DOWN_S
    if span > window_s:
        raise ValueError(
            f"condition {condition!r} with n_kills={n_kills} needs "
            f"{span:.1f}s but the chaos window is {window_s:.1f}s. Raise "
            f"--chaos-duration or lower --kill-count; do not shorten the gap, "
            f"which is the independent variable.")
    return schedule


def chaos_loop_scheduled(stop_evt, containers: dict, events: list,
                         schedule: list, condition: str):
    """Execute a schedule from build_kill_schedule, recording realized timing.

    Records requested AND realized values per kill so realized-vs-requested is
    checkable per run rather than trusted: `gap_s` is what was asked for,
    `realized_gap_s` is measured from that node's previous restart. Also flags
    `killed_while_down`, the failure mode a short gap can produce -- killing a
    container that has not finished coming back is a different fault from the
    one under study and must be visible, not counted as an ordinary kill.
    """
    started = time.time()
    last_restart_at = {}
    for step in schedule:
        wait = step["at_s"] - (time.time() - started)
        if wait > 0 and stop_evt.wait(wait):
            break
        if stop_evt.is_set():
            break
        name = step["target"]
        c = containers[name]
        was_alive = c.is_alive()
        prev = last_restart_at.get(name)
        t0 = time.time()
        c.kill()
        if stop_evt.wait(step["down_for_s"]):
            c.start()
            break
        alive = c.start()
        last_restart_at[name] = time.time()
        events.append({
            "t": t0,
            "target": name,
            "alive_after_restart": alive,
            "down_for_s": step["down_for_s"],
            "restart_count": c.restart_count,
            # --- controlled-schedule provenance (issue #17) ---
            "condition": condition,
            "seq": step["seq"],
            "requested_at_s": step["at_s"],
            "realized_at_s": round(t0 - started, 3),
            "requested_gap_s": step["gap_s"],
            "realized_gap_s": (None if prev is None else round(t0 - prev, 3)),
            "killed_while_down": not was_alive,
        })


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
