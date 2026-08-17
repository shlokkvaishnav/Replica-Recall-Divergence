<div align="center">

# Nano-DB

[![C++17](https://img.shields.io/badge/C%2B%2B-17-orange?style=flat-square&logo=cplusplus)](https://en.cppreference.com/w/cpp/17)
[![Build](https://img.shields.io/github/actions/workflow/status/shlokkvaishnav/Nano-DB/ci.yml?style=flat-square&label=build)](https://github.com/shlokkvaishnav/Nano-DB/actions)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue?style=flat-square)](LICENSE)
[![Docker](https://img.shields.io/badge/docker-ready-2496ED?style=flat-square&logo=docker&logoColor=white)](https://github.com/shlokkvaishnav/Nano-DB/pkgs/container/nano-db)

**A Raft-replicated vector database, built from scratch in C++17.**

> I built a 3-node Raft cluster backed by a custom HNSW engine to find out what actually happens when you kill the leader mid-write.
> Answer: the cluster re-elects in under a second and zero confirmed writes are dropped.

</div>

---

## What this is

A distributed vector database built from first principles in C++17 — no consensus library, no managed queue, no distributed key-value store. Every distributed systems primitive here is implemented from scratch: the Raft log, the quorum write protocol, the consistent hash ring, the epoch fence.

**If you need a production vector database, use [Qdrant](https://qdrant.tech) or [Milvus](https://milvus.io).** This project exists to understand what's inside them. Raft consensus, quorum writes, and scatter-gather fan-out are the mechanisms that make Qdrant work; this codebase implements the same primitives from scratch to make them inspectable, testable, and breakable.

The non-trivial part isn't any one mechanism in isolation — it's making them compose correctly under failures, where the bugs are timing-dependent and only surface under load with random process kills.

That question — what actually happens to a replica under failure — turned into its own research direction: `research/replica_recall/` measures what node-kill chaos does to search quality specifically, on real SIFT1M vectors. Short version: the control plane survives the kill (the demo below shows that); individual replicas silently lose search quality and never recover it, which the demo doesn't show. Full writeup in `research/replica_recall/README.md`, prior-art positioning in `research/RELATED_WORK.md`.

---

## The demo: kill the leader

```
$ ./cluster.sh up
Starting Nano-DB cluster (9 containers)...
Waiting for Raft leader election...
  Elapsed: 8s — leader elected.

Cluster ready.

  API:     http://localhost:8080
  Grafana: http://localhost:3000  (admin / nanodb)

$ python3 scripts/demo_chaos.py

============================================================
  Nano-DB Chaos Demo: Kill the Leader, Lose Zero Writes
============================================================

Cluster is up. Current leader: coordinator-0 (term=3)

[1/3] Inserting 100 vectors (4 concurrent writers)...
  Inserted 100/100 confirmed.

[2/3] Killing the Raft leader...
  Leader: coordinator-0 (term=3)
  Container: nano-db-coordinator-0-1
  Command: docker kill nano-db-coordinator-0-1

  Writing continues through the outage window...

[3/3] Waiting for new leader...

  New leader: coordinator-1 (term=4)
  Election time: 0.71s

  Verifying all confirmed writes are still searchable...

============================================================
  Results
============================================================
  Vectors confirmed before kill:  100
  Writes dropped:                 0
  Election time:                  0.71s
  Cluster element count after:    100

  All confirmed writes survived the leader kill.
  See Grafana (localhost:3000) for failover_total and vectors_total graphs.
```

Raft term jumps 455→456 as coordinator-1 wins the election; shard failovers=0 and insert failures=0 throughout — the Raft layer absorbed the leader kill with zero data-plane disruption:

![Grafana Dashboard](docs/images/grafana.png)

---

## Quick start

```bash
git clone --recurse-submodules https://github.com/shlokkvaishnav/Nano-DB.git
cd Nano-DB
./cluster.sh up
```

Insert a vector:

```bash
curl -X POST localhost:8080/vectors \
  -H "Content-Type: application/json" \
  -d '{"id": "v1", "vector": [0.1, 0.2, ...128 values...], "metadata": "hello"}'
```

Search:

```bash
curl -X POST localhost:8080/search \
  -H "Content-Type: application/json" \
  -d '{"vector": [0.1, 0.2, ...], "k": 5, "consistency": "strong"}'
```

Kill the leader and verify zero data loss:

```bash
python3 scripts/demo_chaos.py
```

Run 60 seconds of continuous random process kills across all 12 nodes:

```bash
./cluster.sh chaos
```

---

## Architecture

![Cluster architecture](docs/images/architecture.png)

**Control plane (Raft group).** Three coordinator nodes form a Raft cluster. The elected leader handles all write coordination — failover decisions, shard membership changes, and primary promotions all flow through Raft consensus. Any coordinator can be killed; the remaining two elect a new leader within a second and resume without data loss.

**Data plane (sharded + replicated).** Vectors are distributed across shards via consistent hashing with 200 virtual nodes. Each shard has 3 replicas. Writes require a quorum — the primary's acknowledgement is mandatory. A write that reaches 2 secondaries but not the primary is correctly rejected, even though it's technically a majority. This matters during failover: the primary is the source of truth.

**Failover.** A background health-check loop on the Raft leader detects primary failures after 3 consecutive missed pings (~3 seconds). It promotes the replica with the highest element count — the most complete one — not just the first reachable one. That distinction was a real bug, found by the chaos harness.

---

## Fault tolerance

Three invariants that the chaos harness validates continuously:

1. **No confirmed write disappears.** Any write that received HTTP 201 (quorum met) must survive any combination of process kills. "Quorum met" means the primary acknowledged and at least one secondary acknowledged.

2. **No split-brain.** No shard ever has two primaries simultaneously. Epoch tokens on every write ensure a demoted primary's in-flight requests are rejected by shards even before the new coordinator detects the failover.

3. **Full recovery.** After chaos stops, the cluster returns to a fully consistent state. No manual intervention required.

To verify yourself:

```bash
./cluster.sh chaos   # 60s of random kills, invariant report at the end
```

---

## Key features

| Category | What's built |
|----------|-------------|
| **Consensus** | Raft from scratch — leader election, log replication, Figure 8 safety, log compaction + InstallSnapshot |
| **Replication** | Primary-replica with quorum writes. Primary's acknowledgement is mandatory, not just majority |
| **Fencing** | Epoch tokens on every write — stale coordinators are rejected by shards after a failover |
| **Failover** | Automatic primary promotion based on replica completeness, not just reachability |
| **Routing** | Consistent hashing with 200 virtual nodes; sequential-ID clustering bug found and fixed |
| **Storage** | Custom HNSW, memory-mapped persistence, SIMD-accelerated (AVX2) distance kernels |
| **Chaos testing** | Continuous random process kills with data integrity invariants validated throughout |
| **Observability** | Prometheus metrics + Grafana dashboard (auto-provisioned) |

---

## Performance

**A caveat before the numbers: most of this table has never been re-measured
since it was first written, and one row (single-node insert) traces to a
benchmark file whose own hardware-documentation comment was never filled in
— see below.** The rows below marked *(native, verified 2026-08)* were
re-run against the current build on a 4-thread i3-1115G4 laptop; treat them
as a lower bound on what this code can do, not a ceiling — they were run on
noticeably weaker hardware than whatever originally produced the numbers
they replace or sit next to.

Docker numbers below were measured with Docker Compose on a single host
(2 shards × 3 replicas + 3 Raft coordinators, Docker bridge network) and were
not re-run this pass. All cluster numbers include HTTP and replication overhead.

| Metric | Value | Notes |
|--------|-------|-------|
| Cluster insert throughput (Docker) | **146 vec/s** | 4 concurrent clients, quorum writes<sup>1</sup> |
| Cluster insert throughput (native) | **213.5 vec/s** *(median, range 191.5–400.3, 5 reps)* | no Docker layer, `--repeat 5` — see footnote 1 |
| Search latency p50 (Docker) | **5.9 ms** | scatter-gather across 2 shards, 167k-vector index |
| Search latency p95 (Docker) | **10.4 ms** | |
| Search latency p99 (Docker) | **27.9 ms** | slowest shard gates the result — see [tail latency](#tail-latency-in-scatter-gather) |
| Search latency p50/p99 (native) | **~39 ms / ~102 ms** *(median across 5 reps, ~6–12k-vector index)* | smaller index, still slower — hardware difference, not a regression; see footnote 1 |
| Failover recovery | **0.5 s** | primary killed, replica promoted by element count |
| Raft leader election | **< 1 s** | randomized 300–600 ms timeouts |
| Single-node insert | **510–1,103 TPS** *(native, verified 2026-08)* | peaks at 2 threads, *declines* to 728 at 8 — see footnote 2 |
| Single-node search | **not currently measured** | `benchmarks/benchmark_throughput.cpp` reports no search-latency number; nothing else in the repo does either |
| Recall@10 | **≤ 81.6%** on synthetic data *(verified 2026-08)*, corpus-dependent | see footnote 3 |

<sup>1</sup> 163 vec/s at 8 concurrent clients; 146 vec/s is the reproducible 4-client Docker result from `benchmarks/cluster_benchmark_results.json` (`./cluster.sh up && python3 benchmarks/cluster_benchmark.py`). The native row is `benchmarks/cluster_throughput.py --repeat 5`, no Docker layer, no artificial pacing, median with explicit range rather than a point estimate — a single run on this host showed ~60% spread. Docker and native are different deployments and the two throughput numbers aren't directly comparable, but for what it's worth native came out faster (less network-stack overhead); native search came out much slower, most likely explained by weaker hardware (see footnote 2) rather than the deployment difference, since a smaller index should search faster, not slower.

<sup>2</sup> `benchmarks/benchmark_throughput.cpp`'s own header has a line reading `Hardware used (fill in before publishing): [e.g. Intel Core i7-12700H, 14 cores, 20 threads]` — an example placeholder, never actually filled in. Whatever number was previously quoted here (6,500 TPS) was measured on unknown hardware that was never documented. The 510–1,103 TPS range is what this exact binary reports today, on a machine with 4 logical threads total — which also explains the throughput *drop* at 8 threads (oversubscription).

<sup>3</sup> `benchmarks/benchmark_recall.cpp` on 100k synthetic vectors, swept over `ef_search` — recall is flat at 46.3% for `ef_search` 10–100, then rises to 81.6% at `ef_search=500`. It does not reach 95% anywhere in the sweep. Synthetic uniform data suffers distance concentration, which is exactly what depresses recall here for reasons unrelated to index quality — see "Recall on synthetic data" in Benchmark methodology below, and `docs/postmortem-recall-bugs.md`. `research/replica_recall/` measures recall against real SIFT1M vectors instead (`--dist sift`) and gets meaningfully higher, corpus-realistic numbers; see that package's README for current figures.

### Benchmark methodology

- **Hardware:** all nodes on a single host via Docker Compose (Docker bridge network round-trip: ~0.1 ms)
- **Warm-up:** 500 vectors inserted before the measurement window opens
- **Query mix:** random 128-dimensional unit vectors, k=10, `"consistency": "strong"`
- **Competitor comparisons** (`benchmarks/compare_against_competitors.py`) measure FAISS and hnswlib as direct in-process library calls with no HTTP or replication overhead — an apples-to-oranges comparison against Nano-DB's cluster numbers, but the right baseline for the single-node storage engine
- **Recall on synthetic data**: both `compare_against_competitors.py` and `benchmarks/benchmark_recall.cpp` generate random synthetic vectors, which suffer distance concentration and depress recall for reasons unrelated to index quality — `benchmark_recall.cpp` says as much in its own comments. `research/replica_recall/` measures recall against real SIFT1M vectors instead (`--dist sift`); see `docs/postmortem-recall-bugs.md` for what synthetic-data recall numbers hid on this project specifically

### Tail latency in scatter-gather

In a fan-out search across N shards, the coordinator must wait for all N shards before merging and returning results. This means:

```
p99_end_to_end ≈ max(p99_shard_0, p99_shard_1, ..., p99_shard_N)
```

p50 stays roughly flat as shard count grows (more parallelism), but p99 worsens monotonically — you're sampling deeper into the tail of the per-shard distribution on every single request. Measured against a live 167k-vector cluster, then projected to higher shard counts using order statistics (`p99 of max(N) = F⁻¹(0.99^{1/N})`):

| Shards | p50 (ms) | p95 (ms) | p99 (ms) | p99.9 (ms) | Notes |
|--------|----------|----------|----------|------------|-------|
| 1 | 5.5 | 10.1 | 19.9 | 26.2 | single shard, no fan-out |
| **2** | **5.5** | **10.1** | **19.9** | **26.2** | **current cluster (measured)** |
| 4 | 6.8 | 16.1 | 25.3 | 26.5 | modeled |
| 8 | 8.3 | 23.0 | 26.1 | 26.5 | modeled |
| 16 | 11.0 | 24.5 | 26.3 | 26.6 | modeled |

The p99 ceiling (~26ms) reflects the hard maximum in this Docker-on-single-host setup where intra-host network jitter is minimal. On a real multi-machine deployment with network-level tail latency, the effect is more pronounced — the modeled values understate the real divergence at scale.

To reproduce: `python3 benchmarks/tail_latency_analysis.py` (requires cluster running).

---

## Raft consensus

![Raft state machine](docs/images/raft-state-machine.png)

The Raft implementation is the centrepiece of this project, built from the paper with no external library.

**Leader election** uses randomized timeouts (300–600 ms) to prevent split votes. A candidate only wins if its log is at least as up-to-date as the voter's — not just term comparison, but a compound check on both term and index that prevents a stale node from becoming leader.

**The Figure 8 commit rule** — the hardest part of Raft — is implemented as a pure function (`compute_new_commit_index`) and tested with a constructed adversarial 5-node scenario plus a mutation test that proves the check is load-bearing, not incidental.

**Log compaction** snapshots the cluster topology every 64 committed entries and installs snapshots on lagging followers instead of replaying full history.

---

## Observability

```bash
./cluster.sh up   # monitoring stack is included
```

Grafana at `localhost:3000` (admin/nanodb) with a pre-built dashboard: cluster throughput, search latency percentiles, Raft term changes, failover events, and per-shard stats. All panels are backed by 14 Prometheus metrics exported at `GET /metrics` on every coordinator.

---

## Testing

**Unit tests (9):** Raft Figure 8 commit safety (adversarial 5-node scenario + mutation test), log compaction, consistent hash ring distribution, deterministic key hashing, ID map store persistence, concurrent config writes, HNSW correctness, SIMD distance accuracy, and mmap persistence. Separately, `research/replica_recall/test_metrics.py` has 75 offline checks across 13 test functions on the measurement core (no cluster required).

```bash
mkdir build && cd build
cmake .. -DCMAKE_BUILD_TYPE=Release -DNANODB_BUILD_CLUSTER=ON
cmake --build . -j$(nproc)
ctest --output-on-failure
```

**Chaos harness** (standalone, no Docker required — runs binaries directly):

```bash
python3 chaos_harness.py --duration 60
```

Orchestrates the full cluster from binaries, runs continuous writes, randomly kills and restarts any of the 12 processes (9 shard replicas + 3 coordinators), and validates the three fault-tolerance invariants throughout.

---

## Building from source

```bash
mkdir build && cd build
cmake .. -DCMAKE_BUILD_TYPE=Release \
         -DNANODB_BUILD_PYTHON=OFF \
         -DNANODB_BUILD_SERVER=ON \
         -DNANODB_BUILD_CLUSTER=ON
cmake --build . -j$(nproc)
ctest --output-on-failure   # 10 tests
```

Requires: CMake 3.16+, g++ 13+, `protobuf-compiler`, `libgrpc++-dev`, `libomp-dev`.

For Docker deployment only, the build happens inside the container — no local toolchain required.
