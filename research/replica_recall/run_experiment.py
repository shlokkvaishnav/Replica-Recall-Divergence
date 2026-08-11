"""
Layer 1 experiment: does per-replica recall diverge under failure, and does
ground-truth-free cross-replica agreement track that divergence?

Reuses chaos_harness.py for process management and fault injection -- the
cluster topology, port layout, kill loop and restart semantics are all
already correct there, and forking them would let the two drift apart.

What this adds on top:
  * a writer that RETAINS the vectors it generates, so exact ground truth
    can be computed locally without pulling vectors back over GetVector
  * a sampler that interrogates every replica directly (probe.py)
  * the three-way metric decomposition (metrics.py)

Run (Linux, after building the binaries):
    pip install grpcio grpcio-tools numpy
    cmake --build build -j            # needs nano_shard_node + nano_coordinator
    python research/replica_recall/run_experiment.py --duration 180

Outputs:
    research/replica_recall/results/samples.csv
    research/replica_recall/results/events.json
    research/replica_recall/results/run_meta.json
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import random
import shutil
import sys
import threading
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)

import chaos_harness as ch                                        # noqa: E402
from metrics import (                                             # noqa: E402
    score_replica, pairwise_agreement, leave_one_out_agreement,
)
import probe as probe_mod                                         # noqa: E402


RESULTS_DIR = os.path.join(HERE, "results")


# ---------------------------------------------------------------------------
# Writer that retains its vectors
# ---------------------------------------------------------------------------

class RetainingWriter:
    """Writes vectors through the coordinators and keeps every vector it
    confirmed, so ground truth needs no round trip to the cluster.

    Records the confirmation timestamp per id because the sampler needs a
    settling window -- see intended_set().
    """

    def __init__(self, dim: int, seed: int, dist: str = "uniform",
                 intrinsic_dim: int = 12, noise: float = 0.02):
        self.dim = dim
        self.lock = threading.Lock()
        self.vector_of: dict[str, np.ndarray] = {}
        self.confirmed_at: dict[str, float] = {}
        self.attempted = 0
        self.failed = 0
        self._next = 0
        self._seed = seed
        self._dist = dist
        self._intrinsic = intrinsic_dim
        self._noise = noise

        # Per-thread generators. numpy Generators are NOT thread-safe, and
        # this ran unlocked across --writers threads -- the vectors were still
        # arbitrary points so the measurements survive, but the stream was
        # undefined and the run was not reproducible from its seed, which it
        # is supposed to be. Seeded from (seed, thread index) so a fixed
        # writer count gives a deterministic corpus.
        self._tls = threading.local()
        self._thread_counter = 0

        if dist == "lowdim":
            # Sample in a low-dimensional subspace and project up. Uniform
            # random points in 128-d suffer distance concentration -- nearest
            # and farthest distances converge, so the true top-k is close to
            # arbitrary and recall falls with N for reasons that have nothing
            # to do with the index. Real embeddings sit at low intrinsic
            # dimensionality; measured on this generator the index holds
            # recall@10 = 1.000 to 20k where uniform gives 0.726.
            proj_rng = np.random.default_rng([seed, 0xC0FFEE])
            self._proj = proj_rng.standard_normal(
                (intrinsic_dim, dim)).astype(np.float32)
        elif dist == "uniform":
            self._proj = None
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

    def _make(self) -> tuple[str, np.ndarray]:
        with self.lock:
            vid = f"rr-{self._next}"
            self._next += 1
        rng = self._rng()
        if self._proj is None:
            vec = rng.random(self.dim, dtype=np.float32) * 2.0 - 1.0
        else:
            z = rng.standard_normal(self._intrinsic).astype(np.float32)
            vec = (z @ self._proj) / np.float32(self._intrinsic)
            vec += rng.standard_normal(self.dim).astype(np.float32) * np.float32(self._noise)
        return vid, vec.astype(np.float32)

    def loop(self, stop_evt: threading.Event, node_ids: list[int]) -> None:
        while not stop_evt.is_set():
            vid, vec = self._make()
            with self.lock:
                self.attempted += 1
            try:
                status, _ = ch.http_request(
                    ch.coord_http_port(random.choice(node_ids)),
                    "POST", "/vectors",
                    {"id": vid, "vector": [float(x) for x in vec]},
                    timeout=2.0)
                if status == 201:
                    with self.lock:
                        self.vector_of[vid] = vec
                        self.confirmed_at[vid] = time.time()
                else:
                    with self.lock:
                        self.failed += 1
            except Exception:
                with self.lock:
                    self.failed += 1
            time.sleep(0.01)

    def make_queries(self, n: int) -> np.ndarray:
        """Query set drawn from the SAME distribution as the corpus.

        Non-negotiable for lowdim: the corpus lives on a 12-dimensional
        manifold inside 128-space, so a uniform query would sit far off it and
        its nearest neighbours would be essentially arbitrary -- measuring
        nothing about the index. Uses its own generator seeded off the run
        seed, so the query set is fixed across samples and reproducible across
        runs without consuming from any writer's stream.
        """
        qrng = np.random.default_rng([self._seed, 0xDEC0DE])
        if self._proj is None:
            return qrng.random((n, self.dim), dtype=np.float32) * 2.0 - 1.0
        z = qrng.standard_normal((n, self._intrinsic)).astype(np.float32)
        q = (z @ self._proj) / np.float32(self._intrinsic)
        q += qrng.standard_normal((n, self.dim)).astype(np.float32) * np.float32(self._noise)
        return q.astype(np.float32)

    def intended_set(self, settle_s: float) -> set[str]:
        """Ids confirmed at least `settle_s` ago.

        The settling window matters methodologically: a write confirmed
        200ms ago may legitimately not have reached every replica yet, and
        counting it would score normal replication lag as data loss. Only
        ids that have had time to propagate are held against a replica.
        """
        cutoff = time.time() - settle_s
        with self.lock:
            return {i for i, t in self.confirmed_at.items() if t <= cutoff}

    def snapshot_vectors(self) -> dict[str, np.ndarray]:
        with self.lock:
            return dict(self.vector_of)


# ---------------------------------------------------------------------------
# Sampler
# ---------------------------------------------------------------------------

def sample_once(probes, writer: RetainingWriter, queries: np.ndarray,
                k: int, settle_s: float, metric: str) -> list[dict]:
    """One full sweep over every replica. Returns a list of row dicts."""
    t = time.time()
    intended_all = writer.intended_set(settle_s)
    vector_of = writer.snapshot_vectors()

    # Phase 1: collect raw observations from every replica.
    # Over-fetch. score_replica must drop results that fall outside the
    # settled intended set before truncating to k; without spare results to
    # draw on, that filtering would just shorten the list and under-report
    # e2e_recall. 3k leaves ample headroom at realistic write rates.
    k_fetch = k * 3

    raw: dict[str, dict] = {}
    for p in probes:
        ok_ids, local = p.list_local_ids()
        ok_search, obs = (p.search_batch(queries, k_fetch) if ok_ids
                          else (False, []))
        raw[p.name] = {
            "probe": p,
            "reachable": bool(ok_ids and ok_search),
            "local": local,
            "obs": obs,
        }

    # Phase 2: per-shard intended set, derived empirically from the union of
    # what the shard's replicas hold (see metrics.py for why not routing).
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

        # Truncated to k: agreement should compare the answer a client would
        # receive, not the over-fetch tail requested for scoring.
        live_obs = {n: [o[:k] for o in raw[n]["obs"]] for n in live}
        agreement = pairwise_agreement(live_obs, k) if len(live) >= 2 else float("nan")
        loo = leave_one_out_agreement(live_obs, k) if len(live) >= 3 else {}

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
                              vector_of, k, metric)

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
            })
    return rows


def sampler_loop(stop_evt, probes, writer, queries, k, settle_s, metric,
                 interval_s, rows_out, errors_out):
    while not stop_evt.is_set():
        try:
            rows_out.extend(sample_once(probes, writer, queries, k, settle_s, metric))
        except Exception as e:                    # a sampler crash must not kill the run
            errors_out.append(repr(e))
        stop_evt.wait(interval_s)


# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--duration", type=int, default=180)
    ap.add_argument("--writers", type=int, default=4)
    ap.add_argument("--queries", type=int, default=100,
                    help="pinned query set size (fixed for the whole run)")
    ap.add_argument("--k", type=int, default=10)
    ap.add_argument("--sample-interval", type=float, default=5.0)
    ap.add_argument("--settle-s", type=float, default=2.0,
                    help="ignore writes confirmed more recently than this")
    ap.add_argument("--warmup-s", type=float, default=20.0,
                    help="write without chaos before fault injection starts")
    ap.add_argument("--metric", default="l2", choices=("l2", "ip"))
    ap.add_argument("--dist", default="uniform", choices=("uniform", "lowdim"),
                    help="vector distribution. 'uniform' is random 128-d, "
                         "which suffers distance concentration and depresses "
                         "recall for reasons unrelated to the index. 'lowdim' "
                         "samples in a 12-d subspace and projects up, the "
                         "regime real embeddings occupy.")
    ap.add_argument("--seed", type=int, default=20260808)
    ap.add_argument("--no-chaos", action="store_true",
                    help="baseline run: measure divergence with no faults")
    ap.add_argument("--chaos-duration", type=int, default=None,
                    help="quiesce protocol: inject faults for this many "
                         "seconds, then STOP and keep sampling for the rest "
                         "of --duration. Answers whether the cluster heals. "
                         "Omit for faults throughout (a steady-state measure, "
                         "which cannot answer that).")
    ap.add_argument("--pre-chaos-s", type=float, default=30.0,
                    help="quiesce protocol only: settle-and-sample window "
                         "before faults start, giving a within-run reference")
    args = ap.parse_args()

    if args.chaos_duration is not None and args.no_chaos:
        print("ERROR: --chaos-duration and --no-chaos are contradictory.",
              file=sys.stderr)
        return 1

    if not os.path.exists(ch.SHARD_NODE_BIN) or not os.path.exists(ch.COORDINATOR_BIN):
        print(f"ERROR: binaries not found ({ch.SHARD_NODE_BIN}, "
              f"{ch.COORDINATOR_BIN}). Build first:\n"
              f"  cmake -B build -DCMAKE_BUILD_TYPE=Release "
              f"-DNANODB_BUILD_CLUSTER=ON && cmake --build build -j",
              file=sys.stderr)
        return 1

    probe_mod.ensure_stubs(os.path.join(ROOT, "proto", "nanodb_cluster.proto"))

    random.seed(args.seed)
    os.makedirs(RESULTS_DIR, exist_ok=True)

    # Pinned query set -- identical at every sample and across runs, so
    # recall differences are attributable to the cluster, not the queries.
    # The writer owns the distribution, so it also mints the query set: a
    # uniform query against a lowdim corpus would sit off the data manifold
    # and measure nothing. Constructed here rather than after cluster startup
    # purely so the queries can come from it; it touches no cluster state.
    writer = RetainingWriter(ch.VECTOR_DIM, args.seed, dist=args.dist)
    queries = writer.make_queries(args.queries)

    shutil.rmtree(ch.RUN_DIR, ignore_errors=True)
    ch.write_initial_configs()
    procs = ch.build_processes()

    print(f"[rr] starting {len(procs)} processes "
          f"({ch.NUM_SHARDS}x{ch.REPLICAS_PER_SHARD} replicas, "
          f"{ch.NUM_COORDINATORS} coordinators)")

    for name in [n for n in procs if n.startswith("shard-")]:
        if not procs[name].start():
            print(f"[rr] FATAL: {name} failed to start; see {procs[name].log_path}")
            return 1
    for name in [n for n in procs if n.startswith("coordinator-")]:
        if not procs[name].start():
            print(f"[rr] FATAL: {name} failed to start; see {procs[name].log_path}")
            return 1

    node_ids = list(range(ch.NUM_COORDINATORS))
    if not ch.wait_for_coordinators_ready(node_ids):
        print("[rr] FATAL: coordinators never became ready")
        for p in procs.values():
            p.kill()
        return 1
    leader = ch.wait_for_raft_leader(node_ids)
    if leader is None:
        print("[rr] FATAL: no raft leader elected")
        for p in procs.values():
            p.kill()
        return 1
    print(f"[rr] cluster ready, raft leader = coordinator-{leader}")

    probes = probe_mod.build_probes(ch.NUM_SHARDS, ch.REPLICAS_PER_SHARD,
                                     ch.shard_port)

    stop_evt = threading.Event()
    rows: list[dict] = []
    sampler_errors: list[str] = []
    chaos_events: list[dict] = []
    threads: list[threading.Thread] = []

    for _ in range(args.writers):
        t = threading.Thread(target=writer.loop, args=(stop_evt, node_ids), daemon=True)
        t.start()
        threads.append(t)

    t_start = time.time()

    print(f"[rr] warmup {args.warmup_s:.0f}s (writing, no faults)...")
    time.sleep(args.warmup_s)

    st = threading.Thread(
        target=sampler_loop,
        args=(stop_evt, probes, writer, queries, args.k, args.settle_s,
              args.metric, args.sample_interval, rows, sampler_errors),
        daemon=True)
    st.start()
    threads.append(st)

    chaos_start_rel = None
    chaos_stop_rel = None

    # Declared out here, not inside the quiesce branch, so the interrupt
    # handler below can always stop the chaos thread. The quiesce path runs
    # chaos off its own event rather than stop_evt; if a Ctrl-C during the
    # fault window left that event unset, chaos_loop would keep killing and
    # restarting nodes straight through teardown and could leave orphans
    # holding the ports -- which then breaks the next run of a sweep.
    chaos_stop_evt = threading.Event()

    try:
        if args.no_chaos:
            print(f"[rr] baseline (no chaos), running {args.duration}s...")
            time.sleep(args.duration)

        elif args.chaos_duration is None:
            # Unchanged behaviour: faults for the whole run. This measures a
            # steady state -- ongoing damage balanced against whatever repair
            # exists -- and so cannot answer whether the cluster heals.
            ct = threading.Thread(target=ch.chaos_loop,
                                  args=(stop_evt, procs, chaos_events),
                                  daemon=True)
            ct.start()
            threads.append(ct)
            chaos_start_rel = time.time() - t_start
            print(f"[rr] chaos ON for the whole run, {args.duration}s...")
            time.sleep(args.duration)
            chaos_stop_rel = time.time() - t_start

        else:
            # Quiesce protocol: settle, break things, then STOP breaking them
            # and keep watching. The question this exists to answer is whether
            # a replica that missed writes while it was down ever gets them
            # back, or stays silently short forever.
            pre = args.pre_chaos_s
            post = args.duration - pre - args.chaos_duration
            if post <= 0:
                print(f"[rr] FATAL: --duration {args.duration} leaves no "
                      f"quiesce window after --pre-chaos-s {pre} + "
                      f"--chaos-duration {args.chaos_duration}.",
                      file=sys.stderr)
                stop_evt.set()
                for p in procs.values():
                    p.kill()
                return 1

            print(f"[rr] phase 1/3: {pre:.0f}s settling, no faults...")
            time.sleep(pre)

            ct = threading.Thread(target=ch.chaos_loop,
                                  args=(chaos_stop_evt, procs, chaos_events),
                                  daemon=True)
            ct.start()
            threads.append(ct)
            chaos_start_rel = time.time() - t_start
            print(f"[rr] phase 2/3: {args.chaos_duration:.0f}s chaos...")
            time.sleep(args.chaos_duration)

            chaos_stop_evt.set()
            ct.join(timeout=15.0)

            # chaos_loop restarts whatever it last killed before exiting, but
            # make certain: a node still down would be scored as unreachable
            # for the whole quiesce window and would mask the healing signal.
            revived = 0
            for name, p in procs.items():
                if not p.is_alive():
                    p.start()
                    revived += 1
            chaos_stop_rel = time.time() - t_start
            if revived:
                print(f"[rr]   ({revived} process(es) restarted at chaos stop)")

            print(f"[rr] phase 3/3: {post:.0f}s quiesce -- faults stopped, "
                  f"watching for recovery...")
            time.sleep(post)

    except KeyboardInterrupt:
        print("\n[rr] interrupted, shutting down early")

    chaos_stop_evt.set()
    stop_evt.set()
    for t in threads:
        t.join(timeout=10.0)

    for p in probes:
        p.close()

    # ---- write results -----------------------------------------------------
    cols = ["t_rel", "shard", "replica", "name", "reachable",
            "index_recall", "e2e_recall", "completeness",
            "n_local", "n_intended", "shard_agreement", "loo_agreement",
            "n_confirmed_settled"]
    csv_path = os.path.join(RESULTS_DIR, "samples.csv")
    with open(csv_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            r = dict(r)
            r["t_rel"] = round(r.pop("t") - t_start, 3)
            w.writerow(r)

    with open(os.path.join(RESULTS_DIR, "events.json"), "w") as f:
        json.dump([{**e, "t_rel": round(e["t"] - t_start, 3)} for e in chaos_events],
                  f, indent=2)

    meta = {
        "duration_s": args.duration,
        "writers": args.writers,
        "queries": args.queries,
        "k": args.k,
        "sample_interval_s": args.sample_interval,
        "settle_s": args.settle_s,
        "warmup_s": args.warmup_s,
        "metric": args.metric,
        "dist": args.dist,
        "seed": args.seed,
        "chaos": not args.no_chaos,
        # Present only for quiesce runs; analyze.py keys the healing report
        # off these, so their absence means "no quiesce window".
        "chaos_duration_s": args.chaos_duration,
        "pre_chaos_s": args.pre_chaos_s if args.chaos_duration else None,
        "chaos_start_rel": (round(chaos_start_rel, 3)
                            if chaos_start_rel is not None else None),
        "chaos_stop_rel": (round(chaos_stop_rel, 3)
                           if chaos_stop_rel is not None else None),
        "quiesce": args.chaos_duration is not None,
        "num_shards": ch.NUM_SHARDS,
        "replicas_per_shard": ch.REPLICAS_PER_SHARD,
        "vector_dim": ch.VECTOR_DIM,
        "samples": len(rows),
        "chaos_events": len(chaos_events),
        "confirmed_total": len(writer.vector_of),
        "write_attempted": writer.attempted,
        "write_failed": writer.failed,
        "sampler_errors": sampler_errors[:20],
    }
    with open(os.path.join(RESULTS_DIR, "run_meta.json"), "w") as f:
        json.dump(meta, f, indent=2)

    print("\n[rr] teardown...")
    for p in procs.values():
        p.kill()

    print(f"[rr] {len(rows)} samples, {len(chaos_events)} chaos events, "
          f"{len(writer.vector_of)} vectors confirmed")
    if sampler_errors:
        print(f"[rr] WARNING: {len(sampler_errors)} sampler errors, "
              f"first: {sampler_errors[0]}")
    print(f"[rr] wrote {csv_path}")
    print(f"[rr] analyse with: python {os.path.join(HERE, 'analyze.py')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
