"""
Cross-system replica-recall experiment: Qdrant.

Answers the question in SPEC.md by running the exact same measurement core
(`metrics.py`, reused unmodified per the isolation rule) against a live
Qdrant cluster instead of nano-db, using qdrant_probe.py's direct
per-replica gRPC path in place of probe.py's ShardService client, and
qdrant_docker_harness.py's Docker container kill/restart in place of
chaos_harness.py's bare-process kill/restart.

This file deliberately mirrors research/replica_recall/run_experiment.py's
structure (RetainingWriter -> sample_once -> sampler_loop -> main) rather
than being written from scratch, so a reader who already understands the
nano-db experiment can follow this one by its differences, not learn it
cold. The differences that matter are: (1) the writer speaks Qdrant's REST
API in small batches rather than nano-db's one-point-per-request HTTP
endpoint -- batching only, not a behavioral change to what gets measured;
(2) chaos targets containers, not processes; (3) the intended-set/shard
semantics come from qdrant_probe's Scroll-based ListLocalIds equivalent.

Run (after `docker compose` access and `pip install grpcio grpcio-tools
qdrant-client numpy`):
    python research/cross_system_replication/qdrant_run_experiment.py --duration 180

Outputs:
    research/cross_system_replication/results/samples.csv
    research/cross_system_replication/results/events.json
    research/cross_system_replication/results/run_meta.json
"""

from __future__ import annotations

import argparse
import csv
import datetime
import json
import os
import random
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
REPLICA_RECALL_DIR = os.path.join(ROOT, "research", "replica_recall")
sys.path.insert(0, HERE)
sys.path.insert(0, REPLICA_RECALL_DIR)

import qdrant_topology as topo                                    # noqa: E402
import qdrant_docker_harness as dh                                # noqa: E402
import qdrant_probe as probe_mod                                  # noqa: E402
import qdrant_index_gate as gate_mod                              # noqa: E402
from metrics import (                                             # noqa: E402
    Corpus, score_replica, pairwise_agreement, leave_one_out_agreement,
)
import sift                                                       # noqa: E402

RESULTS_DIR = os.path.join(HERE, "results")

# Files this script owns in its output directory. Listed explicitly so startup
# can clear them: a failed run must not leave the PREVIOUS run's output sitting
# there looking current. Issue #26 -- a dead Docker daemon once caused 15 runs to
# fail and a sweep to copy one stale predecessor 15 times, producing directories
# of plausible-looking data belonging to an unrelated experiment.
OUTPUT_FILES = ("samples.csv", "events.json", "run_meta.json", "telemetry.csv")
PROTO_DIR = os.path.join(HERE, "proto")


# ---------------------------------------------------------------------------
# Writer that retains its vectors -- identical role to run_experiment.py's
# RetainingWriter, batched over Qdrant's REST upsert instead of one HTTP
# POST per vector.
# ---------------------------------------------------------------------------

class RetainingWriter:
    def __init__(self, dim: int, seed: int, dist: str = "sift",
                 intrinsic_dim: int = 12, noise: float = 0.02,
                 sift_dir: str | None = None, sift_vectors: int = 200_000,
                 batch_size: int = 32):
        self.dim = dim
        self.batch_size = batch_size
        self.lock = threading.Lock()
        self.pause_evt = threading.Event()   # set => writers idle (issue #28)
        self.vector_of: dict[str, np.ndarray] = {}
        self.confirmed_at: dict[str, float] = {}
        self.attempted = 0
        self.failed = 0
        self._next = 0
        self._corpus_ids: list[str] = []
        self._corpus_mat = np.empty((0, dim), dtype=np.float32)
        self._corpus_row: dict[str, int] = {}
        self._corpus_n = 0
        self._seed = seed
        self._dist = dist
        self._intrinsic = intrinsic_dim
        self._noise = noise

        self._tls = threading.local()
        self._thread_counter = 0

        self._sift_base: np.ndarray | None = None
        self._sift_queries: np.ndarray | None = None
        self._sift_order: np.ndarray | None = None
        self.exhausted = False

        if dist == "sift":
            self._proj = None
            base, queries = sift.load(sift_dir, n_base=sift_vectors, dim=dim)
            self._sift_base = base
            self._sift_queries = queries
            self._sift_order = np.random.default_rng(
                [seed, 0x51F7]).permutation(len(base))
        elif dist == "uniform":
            self._proj = None
        elif dist == "lowdim":
            proj_rng = np.random.default_rng([seed, 0xC0FFEE])
            self._proj = proj_rng.standard_normal(
                (intrinsic_dim, dim)).astype(np.float32)
        else:
            raise ValueError(f"unknown --dist: {dist!r}")

    def _rng(self):
        r = getattr(self._tls, "rng", None)
        if r is None:
            with self.lock:
                idx = self._thread_counter
                self._thread_counter += 1
            r = np.random.default_rng([self._seed, idx])
            self._tls.rng = r
        return r

    def _make_batch(self, n: int) -> list[tuple[str, np.ndarray]]:
        """Up to n (id, vector) pairs. Shorter than n once a finite corpus
        (--dist sift) is exhausted; empty means exhausted."""
        with self.lock:
            start = self._next
            if self._sift_base is not None:
                n = min(n, len(self._sift_order) - start)
            self._next += max(n, 0)

        if n <= 0:
            if self._sift_base is not None:
                with self.lock:
                    self.exhausted = True
            return []

        out = []
        if self._sift_base is not None:
            for i in range(start, start + n):
                vid = f"{100_000_000 + i}"      # numeric id, see qdrant_probe.py
                out.append((vid, self._sift_base[self._sift_order[i]]))
            return out

        rng = self._rng()
        for i in range(start, start + n):
            vid = f"{100_000_000 + i}"
            if self._proj is None:
                vec = rng.random(self.dim, dtype=np.float32) * 2.0 - 1.0
            else:
                z = rng.standard_normal(self._intrinsic).astype(np.float32)
                vec = (z @ self._proj) / np.float32(self._intrinsic)
                vec += rng.standard_normal(self.dim).astype(np.float32) * np.float32(self._noise)
            out.append((vid, vec.astype(np.float32)))
        return out

    def loop(self, stop_evt: threading.Event, node_ids: list[int]) -> None:
        while not stop_evt.is_set():
            # Issue #28: the indexing gate holds writers here so the optimizer
            # can catch up on a fixed corpus. Nothing is measured while paused
            # (the sampler has not started yet), so pausing changes when the
            # baseline clock starts, not what any sample sees.
            while self.pause_evt.is_set() and not stop_evt.is_set():
                time.sleep(0.1)
            if stop_evt.is_set():
                return
            batch = self._make_batch(self.batch_size)
            if not batch:
                print("[writer] corpus pool exhausted -- stopping this writer. "
                      "Raise --sift-vectors.", flush=True)
                return
            with self.lock:
                self.attempted += len(batch)
            points = [
                {"id": int(vid), "vector": [float(x) for x in vec]}
                for vid, vec in batch
            ]
            try:
                port = topo.http_port(random.choice(node_ids))
                status, _ = topo.http_request(
                    port, "PUT", f"/collections/{topo.COLLECTION}/points?wait=true",
                    {"points": points}, timeout=10.0)
                if status == 200:
                    t = time.time()
                    with self.lock:
                        for vid, vec in batch:
                            self.vector_of[vid] = vec
                            self.confirmed_at[vid] = t
                            self._append_corpus(vid, vec)
                else:
                    with self.lock:
                        self.failed += len(batch)
            except Exception:
                with self.lock:
                    self.failed += len(batch)
            time.sleep(0.02)

    def make_queries(self, n: int) -> np.ndarray:
        qrng = np.random.default_rng([self._seed, 0xDEC0DE])
        if self._sift_queries is not None:
            pool = self._sift_queries
            if n > len(pool):
                raise ValueError(
                    f"--queries {n} exceeds the {len(pool)} SIFT query vectors")
            pick = qrng.choice(len(pool), size=n, replace=False)
            return np.ascontiguousarray(pool[np.sort(pick)])
        if self._proj is None:
            return qrng.random((n, self.dim), dtype=np.float32) * 2.0 - 1.0
        z = qrng.standard_normal((n, self._intrinsic)).astype(np.float32)
        q = (z @ self._proj) / np.float32(self._intrinsic)
        q += qrng.standard_normal((n, self.dim)).astype(np.float32) * np.float32(self._noise)
        return q.astype(np.float32)

    def intended_set(self, settle_s: float) -> set[str]:
        cutoff = time.time() - settle_s
        with self.lock:
            return {i for i, t in self.confirmed_at.items() if t <= cutoff}

    def _append_corpus(self, vid: str, vec: np.ndarray) -> None:
        if self._corpus_n == len(self._corpus_mat):
            new_cap = max(1024, len(self._corpus_mat) * 2)
            grown = np.empty((new_cap, self.dim), dtype=np.float32)
            grown[:self._corpus_n] = self._corpus_mat[:self._corpus_n]
            self._corpus_mat = grown
        self._corpus_mat[self._corpus_n] = vec
        self._corpus_row[vid] = self._corpus_n
        self._corpus_ids.append(vid)
        self._corpus_n += 1

    def snapshot_corpus(self) -> Corpus:
        with self.lock:
            return Corpus(self._corpus_ids, self._corpus_mat,
                          self._corpus_row, self._corpus_n)


# ---------------------------------------------------------------------------
# Sampler -- identical logic to run_experiment.py's sample_once/sampler_loop,
# against qdrant_probe.ReplicaProbe instead of probe.ReplicaProbe.
# ---------------------------------------------------------------------------

def _chaos_thread(dh_mod, args, stop_evt, containers, events, window_s):
    """Pick the randomized or the controlled chaos loop (issue #17).

    Default (`--kill-schedule` omitted) returns exactly the thread this code
    built before the flag existed -- no already-merged result shifts because a
    new option appeared, the same discipline `--loo-query-mode pinned` follows.
    The schedule is built and validated up front so an infeasible request fails
    before the cluster is under load, not partway through a run.
    """
    if not args.kill_schedule:
        return threading.Thread(target=dh_mod.chaos_loop,
                                args=(stop_evt, containers, events), daemon=True)

    schedule = dh_mod.build_kill_schedule(
        args.kill_schedule, list(containers.keys()), args.kill_count,
        window_s, target_node=args.kill_target_node)
    print(f"[qrr] kill schedule '{args.kill_schedule}': "
          + ", ".join(f"t+{s['at_s']:.1f}s {s['target']}" for s in schedule))
    return threading.Thread(
        target=dh_mod.chaos_loop_scheduled,
        args=(stop_evt, containers, events, schedule, args.kill_schedule),
        daemon=True)


def sample_once(probes, writer: RetainingWriter, queries: np.ndarray,
                k: int, settle_s: float, metric: str) -> list[dict]:
    t = time.time()
    intended_all = writer.intended_set(settle_s)
    corpus = writer.snapshot_corpus()
    k_fetch = k * 3

    def _probe_one(p):
        ok_ids, local = p.list_local_ids()
        ok_search, obs = (p.search_batch(queries, k_fetch) if ok_ids
                          else (False, []))
        return p.name, {
            "probe": p, "reachable": bool(ok_ids and ok_search),
            "local": local, "obs": obs,
        }

    t_probe = time.time()
    raw: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=max(1, len(probes))) as ex:
        for name, rec in ex.map(_probe_one, probes):
            raw[name] = rec
    probe_s = time.time() - t_probe
    t_score = time.time()

    by_shard: dict[int, list[str]] = {}
    for name, r in raw.items():
        by_shard.setdefault(r["probe"].shard_id, []).append(name)

    rows: list[dict] = []
    for shard_id, names in sorted(by_shard.items()):
        live = [n for n in names if raw[n]["reachable"]]
        union = set()
        for n in live:
            union |= raw[n]["local"]
        intended_s = union & intended_all

        live_obs = {n: [o[:k] for o in raw[n]["obs"]] for n in live}
        agreement = pairwise_agreement(live_obs, k) if len(live) >= 2 else float("nan")
        loo = leave_one_out_agreement(live_obs, k) if len(live) >= 3 else {}

        intended_rows = corpus.rows_for(intended_s)

        for n in names:
            r = raw[n]
            p = r["probe"]
            if not r["reachable"]:
                rows.append({
                    "t": t, "shard": shard_id, "replica": p.replica_id,
                    "name": n, "reachable": 0,
                    "index_recall": "", "e2e_recall": "", "completeness": "",
                    "n_local": "", "n_intended": len(intended_s),
                    "shard_agreement": "" if np.isnan(agreement) else round(agreement, 6),
                    "loo_agreement": "",
                    "n_confirmed_settled": len(intended_all),
                })
                continue

            m = score_replica(queries, r["obs"], r["local"], intended_s,
                              corpus, k, metric, intended_rows=intended_rows)

            def fmt(x: float) -> str:
                return "" if np.isnan(x) else f"{x:.6f}"

            rows.append({
                "t": t, "shard": shard_id, "replica": p.replica_id,
                "name": n, "reachable": 1,
                "index_recall": fmt(m["index_recall"]),
                "e2e_recall": fmt(m["e2e_recall"]),
                "completeness": fmt(m["completeness"]),
                "n_local": int(m["n_local"]),
                "n_intended": int(m["n_intended"]),
                "shard_agreement": "" if np.isnan(agreement) else round(agreement, 6),
                "loo_agreement": ("" if np.isnan(loo.get(n, float("nan")))
                                  else round(loo[n], 6)),
                "n_confirmed_settled": len(intended_all),
                "probe_s": round(probe_s, 3),
                "score_s": round(time.time() - t_score, 3),
            })
    return rows


def sampler_loop(stop_evt, probes, writer, queries, k, settle_s, metric,
                 interval_s, rows_out, errors_out):
    while not stop_evt.is_set():
        try:
            rows_out.extend(sample_once(probes, writer, queries, k, settle_s, metric))
        except Exception as e:
            errors_out.append(repr(e))
        stop_evt.wait(interval_s)


# ---------------------------------------------------------------------------
# Cluster bring-up / teardown
# ---------------------------------------------------------------------------

def telemetry_loop(stop_evt, node_ids, interval_s, rows_out, errors_out):
    """experiment/qdrant-optimizer-masking-index-recall (issue #8): polls
    each node's own `/collections/{name}` view -- indexed_vectors_count,
    segments_count, status -- at the same cadence as the probe sampler, so
    HNSW-indexing progress can be correlated against index_recall samples
    after the run. Qdrant's segment-merge/optimizer activity does not show
    up in container logs at INFO or DEBUG level (checked manually before
    building this), but this REST endpoint tracks it directly and reliably.

    Deliberately its own thread/CSV rather than folded into sample_once():
    this is diagnostic instrumentation for one specific confound check, not
    part of the measurement core that other branches' analyze.py depends
    on -- keeping it separate means enabling it can never change samples.csv's
    schema or behavior on runs that don't ask for it.
    """
    while not stop_evt.is_set():
        t = time.time()
        for n in node_ids:
            try:
                status, body = topo.http_request(
                    topo.http_port(n), "GET", f"/collections/{topo.COLLECTION}",
                    timeout=2.0)
                if status == 200:
                    res = body.get("result", {})
                    rows_out.append({
                        "t": t, "node": n,
                        "indexed_vectors_count": res.get("indexed_vectors_count"),
                        "points_count": res.get("points_count"),
                        "segments_count": res.get("segments_count"),
                        "status": res.get("status"),
                        "optimizer_status": res.get("optimizer_status"),
                    })
            except Exception as e:
                errors_out.append(repr(e))
        stop_evt.wait(interval_s)


def bring_up_cluster(node_ids, indexing_threshold_kb: int | None = None) -> bool:
    subprocess.run(["docker", "compose", "-p", topo.PROJECT, "-f", topo.COMPOSE_PATH,
                    "down", "-v"], capture_output=True)
    topo.write_compose_file()
    r = subprocess.run(["docker", "compose", "-p", topo.PROJECT, "-f", topo.COMPOSE_PATH,
                        "up", "-d"], capture_output=True, text=True)
    if r.returncode != 0:
        print(f"[qrr] FATAL: docker compose up failed:\n{r.stderr}", file=sys.stderr)
        return False
    if not topo.wait_for_nodes_ready(node_ids):
        print("[qrr] FATAL: nodes never became ready", file=sys.stderr)
        return False
    if not topo.wait_for_cluster_formed(node_ids):
        print("[qrr] FATAL: cluster never formed (peers did not converge)", file=sys.stderr)
        return False
    if not topo.create_collection(indexing_threshold_kb=indexing_threshold_kb):
        print("[qrr] FATAL: collection creation failed", file=sys.stderr)
        return False
    if not topo.wait_for_shards_active(node_ids):
        print("[qrr] FATAL: shards never became Active", file=sys.stderr)
        return False
    return True


def tear_down_cluster() -> None:
    subprocess.run(["docker", "compose", "-p", topo.PROJECT, "-f", topo.COMPOSE_PATH,
                    "down", "-v"], capture_output=True)


# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--duration", type=int, default=180)
    ap.add_argument("--writers", type=int, default=4)
    ap.add_argument("--queries", type=int, default=100)
    ap.add_argument("--k", type=int, default=10)
    ap.add_argument("--sample-interval", type=float, default=5.0)
    ap.add_argument("--settle-s", type=float, default=3.0,
                    help="widened vs. nano-db's 2.0s default -- Qdrant's "
                         "own replication path adds latency the direct "
                         "gRPC write path in nano-db does not have")
    ap.add_argument("--warmup-s", type=float, default=20.0)
    ap.add_argument("--metric", default="l2", choices=("l2", "ip"))
    ap.add_argument("--dist", default="sift", choices=("uniform", "lowdim", "sift"))
    ap.add_argument("--sift-dir", default=None)
    ap.add_argument("--sift-vectors", type=int, default=200_000)
    ap.add_argument("--seed", type=int, default=20260808)
    ap.add_argument("--no-chaos", action="store_true")
    ap.add_argument("--chaos-duration", type=int, default=None)
    ap.add_argument("--kill-schedule", default=None,
                    choices=list(dh.KILL_CONDITIONS),
                    help="issue #17: replace the randomized chaos loop with a "
                         "controlled kill schedule, making inter-kill spacing "
                         "and targeting independent variables. Omit for the "
                         "existing randomized behaviour (default, unchanged).")
    ap.add_argument("--kill-count", type=int, default=3,
                    help="--kill-schedule only: kills per run, held constant "
                         "across conditions.")
    ap.add_argument("--kill-target-node", default=None,
                    help="--kill-schedule only: which node the same-node "
                         "conditions repeatedly kill (default: node 0).")
    ap.add_argument("--pre-chaos-s", type=float, default=30.0)
    ap.add_argument("--batch-size", type=int, default=32,
                    help="points per REST upsert call (writer efficiency "
                         "only, does not change what is measured)")
    ap.add_argument("--out-dir", default=None,
                    help="where to write samples.csv/events.json/run_meta.json "
                         "(default: this script's results/). A sweep should give "
                         "each run its own directory, so a failed run leaves no "
                         "output rather than leaving its predecessor's -- see #26.")
    ap.add_argument("--capture-telemetry", action="store_true",
                    help="experiment/qdrant-optimizer-masking-index-recall "
                         "(issue #8): poll each node's indexed_vectors_count/"
                         "segments_count/status at --sample-interval "
                         "cadence, written to results/telemetry.csv. Off by "
                         "default -- does not affect samples.csv or any "
                         "other branch's behavior.")
    ap.add_argument("--index-gate", action="store_true",
                    help="issue #28: after --warmup-s, pause the writers and "
                         "block until every replica reports the corpus HNSW-"
                         "indexed (indexed_vectors_count >= (1 - tol) * "
                         "points_count, status green) for --index-gate-"
                         "consecutive polls, THEN start the sampler. A run "
                         "whose gate never closes FAILS (exit 3, no "
                         "samples.csv) rather than measuring an exact scan "
                         "and calling it index_recall. Off by default -- "
                         "existing behaviour unchanged.")
    ap.add_argument("--score-at-gate", action="store_true",
                    help="issue #30 run 0: with --index-gate, score the query "
                         "set on every replica once immediately BEFORE the "
                         "gate (writers paused, tail un-indexed) and once "
                         "immediately AFTER it closes, writing gate_scores.json "
                         "next to run_meta.json. The spot-check PR #29 owed: "
                         "if after-gate index_recall is not lower than before, "
                         "indexed_vectors_count does not mean searches "
                         "traverse the graph. Off by default.")
    ap.add_argument("--index-gate-tol", type=float, default=0.0,
                    help="--index-gate: allowed un-indexed fraction (0.0 = "
                         "every vector on every replica).")
    ap.add_argument("--index-gate-consecutive", type=int, default=3,
                    help="--index-gate: consecutive 1s polls that must pass.")
    ap.add_argument("--index-gate-timeout", type=float, default=600.0,
                    help="--index-gate: seconds before the run gives up and "
                         "fails.")
    ap.add_argument("--warmup-until-written", type=int, default=None,
                    help="issue #28: extend the warmup past --warmup-s until "
                         "at least N vectors are CONFIRMED written, so the "
                         "corpus the gate waits on is a controlled size "
                         "rather than 'whatever --warmup-s allowed'. The "
                         "first gate sweep cell mislabelled a 67k corpus as "
                         "100k this way. Keep --sift-vectors larger than N "
                         "so writers can resume after the gate.")
    ap.add_argument("--warmup-cap-s", type=float, default=600.0,
                    help="--warmup-until-written: give up (run fails, exit 4) "
                         "if N is not reached within this many seconds.")
    ap.add_argument("--indexing-threshold-kb", type=int, default=None,
                    help="issue #28: Qdrant optimizers_config.indexing_"
                         "threshold at collection creation, in KB (Qdrant's "
                         "default is 20000, ~40k 128-d vectors per segment "
                         "before HNSW builds). Unset = Qdrant default; "
                         "recorded in run_meta.json either way.")
    args = ap.parse_args()

    if args.index_gate and args.index_gate_consecutive < 1:
        print("ERROR: --index-gate-consecutive must be >= 1.", file=sys.stderr)
        return 2
    if args.index_gate and not (0.0 <= args.index_gate_tol < 1.0):
        print("ERROR: --index-gate-tol must be in [0, 1).", file=sys.stderr)
        return 2
    if args.kill_schedule and args.no_chaos:
        print("ERROR: --kill-schedule and --no-chaos are contradictory.",
              file=sys.stderr)
        return 2
    if args.chaos_duration is not None and args.no_chaos:
        print("ERROR: --chaos-duration and --no-chaos are contradictory.", file=sys.stderr)
        return 1

    probe_mod.ensure_stubs(PROTO_DIR)

    random.seed(args.seed)

    # Resolve the output directory and CLEAR any output this script owns before
    # doing anything else. Both halves matter (#26): a per-run --out-dir means a
    # failed run leaves no directory to mistake for a result, and clearing means
    # that even when runs share one directory, a failure cannot leave the
    # previous run's files behind to be copied as fresh.
    args.run_id = uuid.uuid4().hex[:12]
    args.run_started_iso = datetime.datetime.now(
        datetime.timezone.utc).isoformat()
    # abspath: a relative --out-dir must mean the same place whether the caller
    # is qdrant_sweep.py (cwd = repo root) or a hand in this directory.
    args.results_dir = results_dir = os.path.abspath(args.out_dir or RESULTS_DIR)
    os.makedirs(results_dir, exist_ok=True)
    for name in OUTPUT_FILES:
        stale = os.path.join(results_dir, name)
        if os.path.exists(stale):
            os.remove(stale)

    writer = RetainingWriter(topo.VECTOR_DIM, args.seed, dist=args.dist,
                             sift_dir=args.sift_dir, sift_vectors=args.sift_vectors,
                             batch_size=args.batch_size)
    queries = writer.make_queries(args.queries)

    node_ids = list(range(topo.REPLICAS_PER_SHARD))
    print(f"[qrr] bringing up {len(node_ids)}-node Qdrant cluster "
          f"({topo.NUM_SHARDS}x{topo.REPLICAS_PER_SHARD}, image={topo.QDRANT_IMAGE})...")
    try:
        # try/finally starts BEFORE bring_up_cluster, not after: its own
        # polling calls (wait_for_nodes_ready, create_collection, ...) can
        # themselves raise -- a bare socket.TimeoutError under host resource
        # contention, seen in practice -- and a bring-up that dies without
        # this leaves the Qdrant containers running, which then breaks
        # every later run in a sweep by squatting on qdrant_topology.py's
        # fixed ports. Two consecutive sweep runs failed exactly this way
        # and had to be torn down and re-run by hand before this fix.
        if not bring_up_cluster(node_ids, args.indexing_threshold_kb):
            return 1
        print("[qrr] cluster ready, collection created, shards Active")
        return _run_experiment_body(args, writer, queries, node_ids)
    finally:
        print("\n[qrr] teardown...")
        tear_down_cluster()


def _run_experiment_body(args, writer, queries, node_ids) -> int:
    containers = dh.build_containers(node_ids)
    probes = probe_mod.build_probes(topo.NUM_SHARDS, topo.REPLICAS_PER_SHARD,
                                    topo.COLLECTION, topo.probe_port_fn)

    stop_evt = threading.Event()
    rows: list[dict] = []
    sampler_errors: list[str] = []
    chaos_events: list[dict] = []
    violations: list = []
    checks_run = [0]
    threads: list[threading.Thread] = []

    for _ in range(args.writers):
        t = threading.Thread(target=writer.loop, args=(stop_evt, node_ids), daemon=True)
        t.start()
        threads.append(t)

    t_start = time.time()
    print(f"[qrr] warmup {args.warmup_s:.0f}s (writing, no faults)...")
    time.sleep(args.warmup_s)

    # Issue #28: a gate on "the corpus" needs the corpus to be a known size.
    # --warmup-s alone makes it write-rate x seconds, which the first sweep
    # cell showed is ~67k when the label said 100k. Extend the warmup until
    # N confirmed writes; fail (exit 4) rather than gate a smaller corpus.
    written_at_gate = None
    if args.warmup_until_written is not None:
        target = args.warmup_until_written
        t_cap = time.time() + args.warmup_cap_s
        last_log = 0.0
        while True:
            with writer.lock:
                n_conf = len(writer.confirmed_at)
                exhausted = writer.exhausted
            if n_conf >= target:
                break
            if exhausted:
                print(f"[qrr] FATAL: corpus pool exhausted at {n_conf} confirmed "
                      f"writes, below --warmup-until-written {target}. Raise "
                      f"--sift-vectors.", file=sys.stderr)
                stop_evt.set()
                return 4
            if time.time() >= t_cap:
                print(f"[qrr] FATAL: only {n_conf}/{target} vectors confirmed "
                      f"within --warmup-cap-s {args.warmup_cap_s:.0f}s.",
                      file=sys.stderr)
                stop_evt.set()
                return 4
            now = time.time() - t_start
            if now - last_log >= 10.0:
                print(f"[qrr] warmup extended: {n_conf}/{target} written at "
                      f"t={now:.0f}s", flush=True)
                last_log = now
            time.sleep(0.5)
        written_at_gate = n_conf
        print(f"[qrr] warmup reached {n_conf} confirmed writes at "
              f"t={time.time() - t_start:.1f}s")

    # Issue #28: hold the writers on a fixed corpus and wait for every
    # replica to report it HNSW-indexed BEFORE the sampler exists. Placed
    # here, after warmup and before the sampler thread, so a closed gate is a
    # property of every sample in the run, and a gate that never closes
    # produces no samples.csv at all -- the run fails the way a dead daemon
    # does (#26), visibly, instead of measuring exact scans and calling it
    # index_recall. The gate's own duration is not part of the baseline
    # window; gate_closed_rel in run_meta.json records where the clock
    # actually started.
    index_gate = None
    if args.index_gate:
        print(f"[qrr] index gate: writers paused, waiting for every replica "
              f"indexed >= {1.0 - args.index_gate_tol:.4f} "
              f"({args.index_gate_consecutive} consecutive polls, "
              f"timeout {args.index_gate_timeout:.0f}s)...")
        writer.pause_evt.set()
        gate_scores = None
        if args.score_at_gate:
            # Issue #30 run 0. Same scorer as the sampler, same query set, on
            # every replica: once with the tail un-indexed, once after the
            # gate closes. HNSW is approximate and exact scan is not, so
            # index_recall should DROP on the tail once it is indexed -- if
            # it does not, "indexed" is a counter, not a search path.
            time.sleep(args.settle_s)
            before = sample_once(probes, writer, queries, args.k, args.settle_s, args.metric)
            gate_scores = {"before_gate": before}
            print("[qrr] score-at-gate BEFORE: " + ", ".join(
                f"{r['name']}={r.get('index_recall')}" for r in before))
        index_gate = gate_mod.wait_for_index_gate(
            node_ids, tol=args.index_gate_tol,
            consecutive=args.index_gate_consecutive,
            timeout_s=args.index_gate_timeout,
            log=lambda m: print(f"[qrr] {m}", flush=True))
        index_gate["gate_opened_rel"] = round(
            time.time() - t_start - index_gate["elapsed_s"], 3)
        index_gate["gate_closed_rel"] = round(time.time() - t_start, 3)
        if not index_gate["closed"]:
            print("[qrr] FATAL: index gate never closed -- refusing to measure "
                  "an un-indexed corpus. No samples.csv written.",
                  file=sys.stderr)
            stop_evt.set()
            for t in threads:
                t.join(timeout=10.0)
            # Leave the gate record where a sweep can see why the run failed.
            with open(os.path.join(args.results_dir, "index_gate_failed.json"), "w") as f:
                json.dump(index_gate, f, indent=2)
            return 3
        if gate_scores is not None:
            after = sample_once(probes, writer, queries, args.k, args.settle_s, args.metric)
            gate_scores["after_gate"] = after
            by = {r["name"]: r for r in before}
            gate_scores["delta_index_recall"] = {
                r["name"]: (None if r.get("index_recall") is None or
                            by.get(r["name"], {}).get("index_recall") is None
                            else round(float(r["index_recall"]) -
                                       float(by[r["name"]]["index_recall"]), 6))
                for r in after}
            gate_scores["gate"] = {k: index_gate.get(k) for k in
                                   ("elapsed_s", "min_fraction_at_end", "per_node_at_end")}
            with open(os.path.join(args.results_dir, "gate_scores.json"), "w") as f:
                json.dump(gate_scores, f, indent=2, default=float)
            print("[qrr] score-at-gate AFTER:  " + ", ".join(
                f"{r['name']}={r.get('index_recall')}" for r in after))
            print("[qrr] score-at-gate delta:  " + ", ".join(
                f"{k}={v}" for k, v in gate_scores["delta_index_recall"].items()))
        writer.pause_evt.clear()
        print(f"[qrr] index gate closed at t={index_gate['gate_closed_rel']:.1f}s; "
              f"writers resumed")

    st = threading.Thread(
        target=sampler_loop,
        args=(stop_evt, probes, writer, queries, args.k, args.settle_s,
              args.metric, args.sample_interval, rows, sampler_errors),
        daemon=True)
    st.start()
    threads.append(st)

    vt = threading.Thread(target=dh.validator_loop,
                          args=(stop_evt, node_ids, violations, checks_run), daemon=True)
    vt.start()
    threads.append(vt)

    telemetry_rows: list[dict] = []
    telemetry_errors: list[str] = []
    if args.capture_telemetry:
        tt = threading.Thread(
            target=telemetry_loop,
            args=(stop_evt, node_ids, args.sample_interval, telemetry_rows, telemetry_errors),
            daemon=True)
        tt.start()
        threads.append(tt)

    chaos_start_rel = None
    chaos_stop_rel = None
    chaos_stop_evt = threading.Event()

    try:
        if args.no_chaos:
            print(f"[qrr] baseline (no chaos), running {args.duration}s...")
            time.sleep(args.duration)

        elif args.chaos_duration is None:
            ct = _chaos_thread(dh, args, stop_evt, containers, chaos_events,
                               args.duration)
            ct.start()
            threads.append(ct)
            chaos_start_rel = time.time() - t_start
            print(f"[qrr] chaos ON for the whole run, {args.duration}s...")
            time.sleep(args.duration)
            chaos_stop_rel = time.time() - t_start

        else:
            pre = args.pre_chaos_s
            post = args.duration - pre - args.chaos_duration
            if post <= 0:
                print(f"[qrr] FATAL: --duration {args.duration} leaves no quiesce "
                      f"window after --pre-chaos-s {pre} + --chaos-duration "
                      f"{args.chaos_duration}.", file=sys.stderr)
                stop_evt.set()
                tear_down_cluster()
                return 1

            print(f"[qrr] phase 1/3: {pre:.0f}s settling, no faults...")
            time.sleep(pre)

            ct = _chaos_thread(dh, args, chaos_stop_evt, containers,
                               chaos_events, args.chaos_duration)
            ct.start()
            threads.append(ct)
            chaos_start_rel = time.time() - t_start
            print(f"[qrr] phase 2/3: {args.chaos_duration:.0f}s chaos...")
            time.sleep(args.chaos_duration)

            chaos_stop_evt.set()
            ct.join(timeout=20.0)

            revived = 0
            for name, c in containers.items():
                if not c.is_alive():
                    c.start()
                    revived += 1
            chaos_stop_rel = time.time() - t_start
            if revived:
                print(f"[qrr]   ({revived} container(s) restarted at chaos stop)")

            print(f"[qrr] phase 3/3: {post:.0f}s quiesce -- faults stopped, "
                  f"watching for recovery...")
            time.sleep(post)

    except KeyboardInterrupt:
        print("\n[qrr] interrupted, shutting down early")

    chaos_stop_evt.set()
    stop_evt.set()
    for t in threads:
        t.join(timeout=10.0)

    for p in probes:
        p.close()

    cols = ["t_rel", "shard", "replica", "name", "reachable",
            "index_recall", "e2e_recall", "completeness",
            "n_local", "n_intended", "shard_agreement", "loo_agreement",
            "n_confirmed_settled", "probe_s", "score_s"]
    extra = sorted({k for r in rows for k in r} - set(cols) - {"t"})
    if extra:
        print(f"[qrr] note: sampler emitted columns missing from the canonical "
              f"list, appending: {', '.join(extra)}")
        cols = cols + extra
    csv_path = os.path.join(args.results_dir, "samples.csv")
    with open(csv_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            r = dict(r)
            r["t_rel"] = round(r.pop("t") - t_start, 3)
            w.writerow(r)

    with open(os.path.join(args.results_dir, "events.json"), "w") as f:
        json.dump([{**e, "t_rel": round(e["t"] - t_start, 3)} for e in chaos_events],
                  f, indent=2)

    if args.capture_telemetry:
        with open(os.path.join(args.results_dir, "telemetry.csv"), "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["t_rel", "node", "indexed_vectors_count",
                                              "points_count", "segments_count",
                                              "status", "optimizer_status"])
            w.writeheader()
            for r in telemetry_rows:
                r = dict(r)
                r["t_rel"] = round(r.pop("t") - t_start, 3)
                w.writerow(r)

    meta = {
        "system": "qdrant",
        "qdrant_image": topo.QDRANT_IMAGE,
        "duration_s": args.duration,
        "writers": args.writers,
        "queries": args.queries,
        "k": args.k,
        "sample_interval_s": args.sample_interval,
        "settle_s": args.settle_s,
        "warmup_s": args.warmup_s,
        "metric": args.metric,
        "dist": args.dist,
        "sift_vectors": args.sift_vectors if args.dist == "sift" else None,
        "corpus_exhausted": writer.exhausted,
        "seed": args.seed,
        "chaos": not args.no_chaos,
        "chaos_duration_s": args.chaos_duration,
        "pre_chaos_s": args.pre_chaos_s if args.chaos_duration else None,
        "chaos_start_rel": (round(chaos_start_rel, 3) if chaos_start_rel is not None else None),
        "chaos_stop_rel": (round(chaos_stop_rel, 3) if chaos_stop_rel is not None else None),
        "quiesce": args.chaos_duration is not None,
        "num_shards": topo.NUM_SHARDS,
        "replicas_per_shard": topo.REPLICAS_PER_SHARD,
        "vector_dim": topo.VECTOR_DIM,
        "samples": len(rows),
        "chaos_events": len(chaos_events),
        "confirmed_total": len(writer.vector_of),
        "write_attempted": writer.attempted,
        "write_failed": writer.failed,
        "sampler_errors": sampler_errors[:20],
        "raft_checks_run": checks_run[0],
        "raft_violations": [repr(v) for v in violations],
        "telemetry_captured": args.capture_telemetry,
        "telemetry_rows": len(telemetry_rows) if args.capture_telemetry else None,
        "telemetry_errors": telemetry_errors[:20] if args.capture_telemetry else None,
        # Provenance (#26). run_id is unique per invocation, so two runs can
        # never produce identical metadata even with identical parameters --
        # which is what made the 15 duplicated directories hard to spot.
        # argv records what was actually asked for, so output can be checked
        # against the request without the consumer remembering it.
        "run_id": args.run_id,
        "started_at": args.run_started_iso,
        "argv": sys.argv[1:],
        # Issue #28: was the corpus indexed before measurement, and on what
        # threshold. index_gate is None when the run did not ask for the
        # gate, so a consumer can tell "not gated" from "gate closed".
        "indexing_threshold_kb": args.indexing_threshold_kb,
        "index_gate": index_gate,
        "warmup_until_written": args.warmup_until_written,
        "written_at_gate": written_at_gate,
    }
    with open(os.path.join(args.results_dir, "run_meta.json"), "w") as f:
        json.dump(meta, f, indent=2)

    print(f"[qrr] {len(rows)} samples, {len(chaos_events)} chaos events, "
          f"{len(writer.vector_of)} vectors confirmed")
    if sampler_errors:
        print(f"[qrr] WARNING: {len(sampler_errors)} sampler errors, "
              f"first: {sampler_errors[0]}")
    if violations:
        print(f"[qrr] WARNING: {len(violations)} raft split-brain violations")
    print(f"[qrr] wrote {csv_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
