"""
Native cluster throughput and latency benchmark.

Measures the two figures the README quotes -- sustained insert throughput and
search latency percentiles -- against a cluster launched directly from the
built binaries, with no Docker layer and no artificial pacing.

Why not reuse the research harness: its writer sleeps 10ms between inserts,
which caps it at ~100/s per thread regardless of how fast the cluster is. It
is built to keep a steady background load while measurement happens
elsewhere, not to find a ceiling.

Why not reuse benchmarks/cluster_benchmark.py: that one targets a Docker
Compose deployment. Numbers from Docker and from native binaries are not
comparable, so a before/after comparison has to hold the environment fixed.

Usage:
    python benchmarks/cluster_throughput.py --duration 30 --writers 8
    python benchmarks/cluster_throughput.py --json results.json

Exits non-zero if the cluster fails to come up.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, ROOT)

import chaos_harness as ch                                        # noqa: E402


def _percentile(xs: list[float], p: float) -> float:
    if not xs:
        return float("nan")
    s = sorted(xs)
    idx = min(len(s) - 1, int(round((p / 100.0) * (len(s) - 1))))
    return s[idx]


class Bench:
    def __init__(self, dim: int, seed: int):
        self.dim = dim
        self.lock = threading.Lock()
        self.ok = 0
        self.failed = 0
        self.latencies: list[float] = []
        self._next = 0
        self._seed = seed
        self._tls = threading.local()
        self._tid = 0

    def _rng(self):
        r = getattr(self._tls, "rng", None)
        if r is None:
            import numpy as np
            with self.lock:
                i = self._tid
                self._tid += 1
            r = np.random.default_rng([self._seed, i])
            self._tls.rng = r
        return r

    def _vec(self):
        return (self._rng().random(self.dim) * 2.0 - 1.0).tolist()

    def insert_loop(self, stop: threading.Event, node_ids: list[int],
                    record: threading.Event) -> None:
        import random as _r
        while not stop.is_set():
            with self.lock:
                vid = f"bench-{self._next}"
                self._next += 1
            vec = self._vec()
            t0 = time.perf_counter()
            try:
                status, _ = ch.http_request(
                    ch.coord_http_port(_r.choice(node_ids)),
                    "POST", "/vectors", {"id": vid, "vector": vec}, timeout=5.0)
                dt = time.perf_counter() - t0
                good = (status == 201)
            except Exception:
                dt = time.perf_counter() - t0
                good = False
            # Only count inside the measurement window, so warmup and the
            # ragged tail after stop do not pollute the rate.
            if record.is_set():
                with self.lock:
                    if good:
                        self.ok += 1
                        self.latencies.append(dt)
                    else:
                        self.failed += 1

    def search_probe(self, node_ids: list[int], n: int, k: int,
                     consistency: str) -> list[float]:
        import random as _r
        out = []
        for _ in range(n):
            vec = self._vec()
            t0 = time.perf_counter()
            try:
                status, _ = ch.http_request(
                    ch.coord_http_port(_r.choice(node_ids)),
                    "POST", "/search",
                    {"vector": vec, "k": k, "consistency": consistency},
                    timeout=5.0)
                if status == 200:
                    out.append(time.perf_counter() - t0)
            except Exception:
                pass
        return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--duration", type=int, default=30)
    ap.add_argument("--warmup", type=int, default=10)
    ap.add_argument("--writers", type=int, default=8)
    ap.add_argument("--searches", type=int, default=300)
    ap.add_argument("--k", type=int, default=10)
    ap.add_argument("--consistency", default="strong", choices=("strong", "eventual"))
    ap.add_argument("--seed", type=int, default=20260808)
    ap.add_argument("--label", default="", help="tag recorded in the JSON output")
    ap.add_argument("--json", default=None, help="write results here")
    args = ap.parse_args()

    if not os.path.exists(ch.SHARD_NODE_BIN) or not os.path.exists(ch.COORDINATOR_BIN):
        print(f"ERROR: binaries not found. Build first.", file=sys.stderr)
        return 1

    import shutil
    shutil.rmtree(ch.RUN_DIR, ignore_errors=True)
    ch.write_initial_configs()
    procs = ch.build_processes()

    for name in [n for n in procs if n.startswith("shard-")]:
        if not procs[name].start():
            print(f"FATAL: {name} failed to start")
            return 1
    for name in [n for n in procs if n.startswith("coordinator-")]:
        if not procs[name].start():
            print(f"FATAL: {name} failed to start")
            return 1

    node_ids = list(range(ch.NUM_COORDINATORS))
    if not ch.wait_for_coordinators_ready(node_ids) or \
            ch.wait_for_raft_leader(node_ids) is None:
        print("FATAL: cluster never became ready")
        for p in procs.values():
            p.kill()
        return 1

    bench = Bench(ch.VECTOR_DIM, args.seed)
    stop = threading.Event()
    record = threading.Event()
    threads = []
    for _ in range(args.writers):
        t = threading.Thread(target=bench.insert_loop,
                             args=(stop, node_ids, record), daemon=True)
        t.start()
        threads.append(t)

    print(f"[bench] warmup {args.warmup}s...")
    time.sleep(args.warmup)

    print(f"[bench] measuring inserts for {args.duration}s "
          f"({args.writers} writers, no pacing)...")
    record.set()
    t_start = time.perf_counter()
    time.sleep(args.duration)
    elapsed = time.perf_counter() - t_start
    record.clear()

    with bench.lock:
        ok, failed = bench.ok, bench.failed
        lat = list(bench.latencies)

    print(f"[bench] {args.searches} searches (consistency={args.consistency})...")
    slat = bench.search_probe(node_ids, args.searches, args.k, args.consistency)

    stop.set()
    for t in threads:
        t.join(timeout=5.0)

    total = ok + failed
    res = {
        "label": args.label,
        "writers": args.writers,
        "duration_s": round(elapsed, 3),
        "insert_ok": ok,
        "insert_failed": failed,
        "insert_per_s": round(ok / elapsed, 1) if elapsed else 0.0,
        "insert_error_rate": round(failed / total, 4) if total else 0.0,
        "insert_p50_ms": round(_percentile(lat, 50) * 1000, 3),
        "insert_p95_ms": round(_percentile(lat, 95) * 1000, 3),
        "insert_p99_ms": round(_percentile(lat, 99) * 1000, 3),
        "search_n": len(slat),
        "search_consistency": args.consistency,
        "search_p50_ms": round(_percentile(slat, 50) * 1000, 3),
        "search_p95_ms": round(_percentile(slat, 95) * 1000, 3),
        "search_p99_ms": round(_percentile(slat, 99) * 1000, 3),
        "search_mean_ms": round(statistics.mean(slat) * 1000, 3) if slat else None,
        "vector_dim": ch.VECTOR_DIM,
        "shards": ch.NUM_SHARDS,
        "replicas_per_shard": ch.REPLICAS_PER_SHARD,
        "rpc_pool_threads": os.environ.get("NANODB_RPC_POOL_THREADS", "default"),
    }

    print("\n[bench] tearing down...")
    for p in procs.values():
        p.kill()

    print()
    print(f"  insert throughput : {res['insert_per_s']:>8.1f} /s   "
          f"({ok} ok, {failed} failed)")
    print(f"  insert p50/p95/p99: {res['insert_p50_ms']:>7.2f} / "
          f"{res['insert_p95_ms']:.2f} / {res['insert_p99_ms']:.2f} ms")
    print(f"  search p50/p95/p99: {res['search_p50_ms']:>7.2f} / "
          f"{res['search_p95_ms']:.2f} / {res['search_p99_ms']:.2f} ms "
          f"(n={len(slat)})")

    if args.json:
        with open(args.json, "w") as f:
            json.dump(res, f, indent=2)
        print(f"\n[bench] wrote {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
