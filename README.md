<div align="center">

# Nano-DB-Replica-Recall

[![C++17](https://img.shields.io/badge/C%2B%2B-17-orange?style=flat-square&logo=cplusplus)](https://en.cppreference.com/w/cpp/17)
[![Build](https://img.shields.io/github/actions/workflow/status/shlokkvaishnav/Nano-DB-Replica-Recall/ci.yml?style=flat-square&label=build)](https://github.com/shlokkvaishnav/Nano-DB-Replica-Recall/actions)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue?style=flat-square)](LICENSE)
[![Docker](https://img.shields.io/badge/docker-ready-2496ED?style=flat-square&logo=docker&logoColor=white)](https://github.com/shlokkvaishnav/Nano-DB-Replica-Recall/pkgs/container/nano-db)

**Does search quality silently diverge across replicas of an approximate index under node failure — and does it ever come back?**

</div>

---

## Research question

Does node-failure chaos cause measurable, replica-level search-quality divergence in a replicated HNSW-based vector database; does that divergence persist after full cluster recovery; and can a ground-truth-free peer-agreement signal detect the degraded replica?

## Why this matters

An exact key-value store that loses a row returns *not found* — an observable event a consistency checker can flag. An approximate nearest-neighbour index that loses vectors, or whose graph degrades, still returns *k* plausible-looking neighbours. Nothing about the response says anything is wrong. **Approximation converts data loss into silence.** No published Jepsen-style analysis has ever targeted a vector database; no streaming-ANN benchmark injects node failure; no production anti-entropy mechanism (Weaviate's hash-tree replication, Vespa's checksummed reconciliation, Dynamo/Cassandra Merkle repair) can be pointed at an ANN graph, because two correct HNSW graphs over identical data differ bit-for-bit. Full positioning against prior work: [`research/RELATED_WORK.md`](research/RELATED_WORK.md).

## What we investigate

`research/replica_recall/` runs a controlled node-kill chaos protocol against a live cluster and probes each replica directly (bypassing the coordinator's scatter-gather, which would merge replicas and hide the divergence). It decomposes "the search is bad" into three ground-truth-backed measurements that move independently — `index_recall` (graph quality, data held constant), `completeness` (data content, no search involved), `e2e_recall` (what a client actually experiences) — plus a fourth, `agreement`, computed with **no ground truth**, to test whether peer disagreement can substitute for it. A quiesce protocol (stop chaos, keep watching) then separates *transient replication lag* from *permanent, unrecovered loss*.

## Current findings

**ESTABLISHED** — supported directly by the experiments in this repo:
> On this system (nano-db: 2 shards × 3 replicas, from-scratch C++ HNSW + Raft), under node-kill chaos on real SIFT1M data (5 seeds), `index_recall` and `completeness` both degrade measurably and statistically significantly versus a no-chaos baseline (exact Mann-Whitney at the n=5 floor, p = 0.0079). Independently-built healthy replicas agree to 1e-4, so ordinary ANN nondeterminism does not explain the gap. Missing data has not returned in any observed post-recovery window.

**HYPOTHESIS** — under active investigation, not yet confirmed:
> That a ground-truth-free peer-agreement statistic (`loo_agreement`) can identify the degraded replica above chance, making it usable as a production detector for a failure mode that is currently invisible. That this failure mode generalizes beyond this one implementation.

**OPEN** — unresolved questions this repo does not answer:
> The root cause of why `index_recall` degrades under chaos. A dedicated forensic tool (`graph_forensics.py`) found no average difference in neighbour-list quality between baseline and chaos replicas — except one replica, never itself killed, that lost reachability to 58.7% of its own graph while every structural check on it looked clean. Two specific hypotheses were tested and ruled out with clean reproductions; the actual mechanism is still unknown. Full writeup: [`docs/postmortems/catastrophic-disconnection.md`](docs/postmortems/catastrophic-disconnection.md). Whether the divergence effect scales with corpus size is also untested.

**DO NOT CLAIM** — statements this evidence does not support:
> "Approximate indexes have no observable correctness criterion under replication" as a general claim (true as a motivating intuition, unproven beyond n=1 system). "Vector databases silently lose data" in general (Milvus #37703 shows a genuinely *loud* failure — the honest claim is that approximation *permits* silence, not that it's universal). "No vector DB repairs missing data" (Weaviate/Vespa do, at the object level — the gap is that object-level repair cannot see graph-level damage). "We understand why recall degrades" (mechanism is open, see above). Anything implying this generalizes to Qdrant, Milvus, Weaviate, or production deployments — untested.

## Methodology, in brief

- **Probes bypass the coordinator** — direct gRPC calls to each replica, so scatter-gather can't average the divergence away.
- **A settling window** (default 2s) prevents normal replication lag from being counted as loss.
- **A baseline-first protocol** — every chaos run is compared against a no-fault run on the same corpus, because HNSW insertion order alone produces some cross-replica disagreement even when nothing is broken.
- **Corpus choice is load-bearing.** Uniform-random vectors suffer distance concentration and hide the effect entirely (p = 0.31); real SIFT1M data separates cleanly (p = 0.0079). A benchmark built on synthetic data would have concluded there was nothing to find.
- **The seed sweep is what's reported**, not a single run — an exact two-sided Mann-Whitney U test compares 5 seeds per condition.

Full methodology, every design decision and why, known limits: [`research/replica_recall/README.md`](research/replica_recall/README.md).

## Repository structure

```
research/                    the research: methodology, experiments, findings
  README.md                  research contract + experiment index
  RELATED_WORK.md            literature positioning, what's already claimed by others
  replica_recall/            Layer 1 — the measurement harness (see its own README)
    RESULTS.md               raw-data status (currently unpopulated — see below)

docs/
  postmortems/                two historical investigations that fed the research
    recall-bugs.md            how the original recall-measurement bugs were found
    catastrophic-disconnection.md   the open 58.7%-loss investigation
  architecture/               reference material for the experimental system itself
    INTERNALS.md              Raft / HNSW / storage engine internals
    images/

benchmarks/
  research/                  benchmark_recall.cpp — load-bearing: the tool whose
                              46% recall reading triggered the recall-bug investigation
  portfolio/                 general perf benchmarks, not part of the research findings

demo/                        chaos-tolerance demo (cluster.sh, demo_chaos.py) —
                              showcases the system, not the research

cluster/, include/, src/, proto/, tests/    the experimental system (Raft + HNSW +
                                             gRPC replication) — infrastructure the
                                             research runs on, not the contribution
```

`cluster/`, `include/`, `src/`, `proto/`, and `tests/` implement nano-db, the vector database this research measures. **Nano-DB is the experimental system, not the research contribution** — the contribution is the measurement methodology and findings in `research/`.

## Reproducing the experiment

Requires Linux with the cluster binaries built (the harness launches processes directly; no Docker).

```bash
pip install grpcio grpcio-tools numpy
cmake -B build -DCMAKE_BUILD_TYPE=Release -DNANODB_BUILD_CLUSTER=ON
cmake --build build -j$(nproc)

python research/replica_recall/run_experiment.py --duration 180 --no-chaos   # baseline
mv research/replica_recall/results research/replica_recall/results_baseline
python research/replica_recall/run_experiment.py --duration 300              # chaos

python research/replica_recall/analyze.py
```

For the full seed sweep this project's numbers are reported from, corpus choice, the quiesce/healing protocol, and graph forensics: [`research/replica_recall/README.md`](research/replica_recall/README.md).

## Current results

The numbers in **Current findings** above come from a 5-seed sweep on real SIFT1M data. **Raw per-seed results are not currently committed to this repository** — the harness requires Linux and built cluster binaries, and has not been (re-)run in every development environment this project has used. [`research/replica_recall/RESULTS.md`](research/replica_recall/RESULTS.md) documents this gap explicitly and gives the exact commands to regenerate the data. No numbers here are backfilled or estimated.

## Limitations

Single system, single from-scratch implementation — not yet shown to generalize (the single biggest open question). 5 seeds, which sits at the exact statistical floor for the rank test used (p = 0.0079 is the smallest attainable value at n=5, so it indicates the groups separate completely rather than that the effect is large). Ground truth is brute-force, practical only to ~10⁵–10⁶ vectors — a mechanism study, not a scale study. `chaos_harness.py` uses SIGKILL, which does not lose dirty mmap pages — machine-level crash consistency is a separate, unaddressed gap. Full list: the "Known limits" section of [`research/replica_recall/README.md`](research/replica_recall/README.md#known-limits).

## Open research questions / next experiments

1. **Cross-system replication** (highest priority) — point the same probe/decompose/quiesce protocol at a second real vector database (Qdrant or Weaviate). This is the step that would turn a measurement of one system into a contribution about the field.
2. **Root-cause closure** on the 58.7%-loss anomaly.
3. **Larger seed count or bootstrap confidence intervals**, beyond the n=5 statistical floor.
4. **Scale sensitivity** beyond the current brute-force ground-truth cap.
5. **Detector robustness** — does `loo_agreement` still work against non-pinned, realistic query traffic?

## Related work

The closest prior art is Wang et al.'s *Towards Reliable Vector Database Management Systems* (arXiv:2502.20812), which names the ANN "oracle problem" but never discusses replication or fault injection. Streaming-ANN benchmarks (FreshDiskANN, SPFresh, the NeurIPS'23 Big-ANN track) measure recall decay under churn on one machine, with no replication dimension. Jepsen-family checkers assume a read has one correct value, which an approximate index doesn't have. Full positioning, per-paper summaries, and — importantly — the specific claims this project must *not* make because prior work already covers them: [`research/RELATED_WORK.md`](research/RELATED_WORK.md).

## License / citation

MIT — see [`LICENSE`](LICENSE). If this work is useful, please cite the repository; a formal citation format will be added if/when this research is written up for submission (see `research/RELATED_WORK.md` for candidate venues).

---

## Appendix: the experimental system

Everything below describes **Nano-DB**, the Raft-replicated vector database the research above runs on — infrastructure, not the research contribution.

### What it is

A distributed vector database built from first principles in C++17 — no consensus library, no managed queue, no distributed key-value store. Every distributed systems primitive here is implemented from scratch: the Raft log, the quorum write protocol, the consistent hash ring, the epoch fence.

**If you need a production vector database, use [Qdrant](https://qdrant.tech) or [Milvus](https://milvus.io).** This project exists to understand what's inside them, and to have a controllable, inspectable system to run the replica-recall research on.

### The demo: kill the leader

This shows the control plane surviving a leader kill with zero dropped writes — a different, narrower guarantee than the research above, which is about search *quality*, not write loss. The control plane demo below does not show replica-level recall divergence; that's what `research/replica_recall/` measures.

```
$ ./demo/cluster.sh up
Starting Nano-DB cluster (9 containers)...
Waiting for Raft leader election...
  Elapsed: 8s — leader elected.

Cluster ready.

  API:     http://localhost:8080
  Grafana: http://localhost:3000  (admin / nanodb)

$ python3 demo/demo_chaos.py

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

![Grafana Dashboard](docs/architecture/images/grafana.png)

### Quick start

```bash
git clone --recurse-submodules https://github.com/shlokkvaishnav/Nano-DB-Replica-Recall.git
cd Nano-DB-Replica-Recall
./demo/cluster.sh up
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
python3 demo/demo_chaos.py
```

Run 60 seconds of continuous random process kills across all 12 nodes:

```bash
./demo/cluster.sh chaos
```

### Architecture

![Cluster architecture](docs/architecture/images/architecture.png)

**Control plane (Raft group).** Three coordinator nodes form a Raft cluster. The elected leader handles all write coordination — failover decisions, shard membership changes, and primary promotions all flow through Raft consensus. Any coordinator can be killed; the remaining two elect a new leader within a second and resume without data loss.

**Data plane (sharded + replicated).** Vectors are distributed across shards via consistent hashing with 200 virtual nodes. Each shard has 3 replicas. Writes require a quorum — the primary's acknowledgement is mandatory. A write that reaches 2 secondaries but not the primary is correctly rejected, even though it's technically a majority. This matters during failover: the primary is the source of truth.

**Failover.** A background health-check loop on the Raft leader detects primary failures after 3 consecutive missed pings (~3 seconds). It promotes the replica with the highest element count — the most complete one — not just the first reachable one. That distinction was a real bug, found by the chaos harness.

Deeper reference on every subsystem above (mmap storage layout, node layout, HNSW graph, SIMD distance kernels, consistent hashing, gRPC RPC, Raft consensus, failover, observability): [`docs/architecture/INTERNALS.md`](docs/architecture/INTERNALS.md).

### Fault tolerance

Three invariants that the chaos harness validates continuously:

1. **No confirmed write disappears.** Any write that received HTTP 201 (quorum met) must survive any combination of process kills. "Quorum met" means the primary acknowledged and at least one secondary acknowledged.

2. **No split-brain.** No shard ever has two primaries simultaneously. Epoch tokens on every write ensure a demoted primary's in-flight requests are rejected by shards even before the new coordinator detects the failover.

3. **Full recovery.** After chaos stops, the cluster returns to a fully consistent state. No manual intervention required.

These are the invariants nano-db's own fault-tolerance harness checks — a stronger, different, and narrower guarantee than what `research/replica_recall/` measures (search *quality* per replica, not write survival at the cluster level).

```bash
./demo/cluster.sh chaos   # 60s of random kills, invariant report at the end
```

### Key features

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

### Performance

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
| Single-node search | **not currently measured** | `benchmarks/portfolio/benchmark_throughput.cpp` reports no search-latency number; nothing else in the repo does either |
| Recall@10 | **≤ 81.6%** on synthetic data *(verified 2026-08)*, corpus-dependent | see footnote 3 |

<sup>1</sup> 163 vec/s at 8 concurrent clients; 146 vec/s is the reproducible 4-client Docker result from `benchmarks/portfolio/cluster_benchmark_results.json` (`./demo/cluster.sh up && python3 benchmarks/portfolio/cluster_benchmark.py`). The native row is `benchmarks/portfolio/cluster_throughput.py --repeat 5`, no Docker layer, no artificial pacing, median with explicit range rather than a point estimate — a single run on this host showed ~60% spread. Docker and native are different deployments and the two throughput numbers aren't directly comparable, but for what it's worth native came out faster (less network-stack overhead); native search came out much slower, most likely explained by weaker hardware (see footnote 2) rather than the deployment difference, since a smaller index should search faster, not slower.

<sup>2</sup> `benchmarks/portfolio/benchmark_throughput.cpp`'s own header has a line reading `Hardware used (fill in before publishing): [e.g. Intel Core i7-12700H, 14 cores, 20 threads]` — an example placeholder, never actually filled in. Whatever number was previously quoted here (6,500 TPS) was measured on unknown hardware that was never documented. The 510–1,103 TPS range is what this exact binary reports today, on a machine with 4 logical threads total — which also explains the throughput *drop* at 8 threads (oversubscription).

<sup>3</sup> `benchmarks/research/benchmark_recall.cpp` on 100k synthetic vectors, swept over `ef_search` — recall is flat at 46.3% for `ef_search` 10–100, then rises to 81.6% at `ef_search=500`. It does not reach 95% anywhere in the sweep. Synthetic uniform data suffers distance concentration, which is exactly what depresses recall here for reasons unrelated to index quality — see "Recall on synthetic data" below, and [`docs/postmortems/recall-bugs.md`](docs/postmortems/recall-bugs.md). `research/replica_recall/` measures recall against real SIFT1M vectors instead (`--dist sift`) and gets meaningfully higher, corpus-realistic numbers — this is the number to trust; see that package's README for current figures.

#### Benchmark methodology

- **Hardware:** all nodes on a single host via Docker Compose (Docker bridge network round-trip: ~0.1 ms)
- **Warm-up:** 500 vectors inserted before the measurement window opens
- **Query mix:** random 128-dimensional unit vectors, k=10, `"consistency": "strong"`
- **Competitor comparisons** (`benchmarks/portfolio/compare_against_competitors.py`) measure FAISS and hnswlib as direct in-process library calls with no HTTP or replication overhead — an apples-to-oranges comparison against Nano-DB's cluster numbers, but the right baseline for the single-node storage engine
- **Recall on synthetic data**: both `compare_against_competitors.py` and `benchmarks/research/benchmark_recall.cpp` generate random synthetic vectors, which suffer distance concentration and depress recall for reasons unrelated to index quality — `benchmark_recall.cpp` says as much in its own comments. `research/replica_recall/` measures recall against real SIFT1M vectors instead (`--dist sift`); see [`docs/postmortems/recall-bugs.md`](docs/postmortems/recall-bugs.md) for what synthetic-data recall numbers hid on this project specifically — this is the origin story of the whole research direction above

#### Tail latency in scatter-gather

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

To reproduce: `python3 benchmarks/portfolio/tail_latency_analysis.py` (requires cluster running).

### Raft consensus

![Raft state machine](docs/architecture/images/raft-state-machine.png)

The Raft implementation is the centrepiece of this system, built from the paper with no external library.

**Leader election** uses randomized timeouts (300–600 ms) to prevent split votes. A candidate only wins if its log is at least as up-to-date as the voter's — not just term comparison, but a compound check on both term and index that prevents a stale node from becoming leader.

**The Figure 8 commit rule** — the hardest part of Raft — is implemented as a pure function (`compute_new_commit_index`) and tested with a constructed adversarial 5-node scenario plus a mutation test that proves the check is load-bearing, not incidental.

**Log compaction** snapshots the cluster topology every 64 committed entries and installs snapshots on lagging followers instead of replaying full history.

### Observability

```bash
./demo/cluster.sh up   # monitoring stack is included
```

Grafana at `localhost:3000` (admin/nanodb) with a pre-built dashboard: cluster throughput, search latency percentiles, Raft term changes, failover events, and per-shard stats. All panels are backed by 14 Prometheus metrics exported at `GET /metrics` on every coordinator.

### Testing

**Unit tests (9):** Raft Figure 8 commit safety (adversarial 5-node scenario + mutation test), log compaction, consistent hash ring distribution, deterministic key hashing, ID map store persistence, concurrent config writes, HNSW correctness, SIMD distance accuracy, and mmap persistence. Separately, `research/replica_recall/test_metrics.py` has 75 offline checks across 13 test functions on the measurement core (no cluster required) — this is the test suite the research findings themselves depend on.

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

Orchestrates the full cluster from binaries, runs continuous writes, randomly kills and restarts any of the 12 processes (9 shard replicas + 3 coordinators), and validates the three fault-tolerance invariants throughout. This is the same fault-injection engine `research/replica_recall/` reuses for its chaos protocol.

### Building from source

```bash
mkdir build && cd build
cmake .. -DCMAKE_BUILD_TYPE=Release \
         -DNANODB_BUILD_PYTHON=OFF \
         -DNANODB_BUILD_SERVER=ON \
         -DNANODB_BUILD_CLUSTER=ON
cmake --build . -j$(nproc)
ctest --output-on-failure   # 9 tests
```

Requires: CMake 3.16+, g++ 13+, `protobuf-compiler`, `libgrpc++-dev`, `libomp-dev`.

For Docker deployment only, the build happens inside the container — no local toolchain required.
