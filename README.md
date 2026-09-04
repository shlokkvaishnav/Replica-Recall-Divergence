<div align="center">

# Replica-Recall-Divergence

[![C++17](https://img.shields.io/badge/C%2B%2B-17-orange?style=flat-square&logo=cplusplus)](https://en.cppreference.com/w/cpp/17)
[![Build](https://img.shields.io/github/actions/workflow/status/shlokkvaishnav/Replica-Recall-Divergence/ci.yml?style=flat-square&label=build)](https://github.com/shlokkvaishnav/Replica-Recall-Divergence/actions)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue?style=flat-square)](LICENSE)
[![Docker](https://img.shields.io/badge/docker-ready-2496ED?style=flat-square&logo=docker&logoColor=white)](https://github.com/shlokkvaishnav/Replica-Recall-Divergence/pkgs/container/nano-db)

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
>
> On a second system (Qdrant, same 2 × 3 topology, `docker kill` chaos, 100k SIFT1M, 5 new seeds), the graph-quality divergence reproduces **at the replica level**: with the corpus HNSW-indexed before measurement and each sample conditioned on its replica being ≥95% indexed, the worst replica's `index_recall` under chaos is 0.978 vs 0.990 at baseline, every seed separated (p = 0.0079), and the worst replica is the killed node in 4 of 5 runs. The cluster-wide six-replica mean does **not** separate (p = 0.31) — the loss is one replica's, ~1.2 points on the seed mean and up to 5 on that replica, and averaging hides it. `completeness` and `e2e_recall` separate on Qdrant as before. See [`research/qdrant_gated_index_recall/`](research/qdrant_gated_index_recall/) (PR #31) and the instrument that made it measurable, [`research/qdrant_index_gate/`](research/qdrant_index_gate/) (PR #29).
>
> **That loss is a transient of the restart.** Watched for 180s after chaos stopped (≈36 post-chaos samples against the 4–5 an earlier 50s window gave), the worst replica's `index_recall` is back inside its own no-chaos range in **4 of 4 judged seeds**: after the last kill one seed dropped to 0.946 for a single 30s bin, one dipped 0.003 for one bin, and two showed nothing beyond noise — every dip gone by the next bin. The fifth seed is **unmeasured**, not healed: its chaos window fired zero kills (a harness defect, [#38](https://github.com/shlokkvaishnav/Replica-Recall-Divergence/issues/38)). Completeness recovered **100%** of missing ids in all four, where the 50s window had seen 0–100%. On one host, at k = 10 over 100k SIFT, on a metric with ~1% of headroom; the closest seed clears its baseline by 0.0002, i.e. is indistinguishable from it rather than above it. This is the opposite of nano-db's result in the paragraph above — but the two were observed on different horizons and different axes, so it is a difference between two measurements, not yet a demonstrated architectural difference. See [`research/qdrant_index_recall_healing/`](research/qdrant_index_recall_healing/) (PR #37).

**HYPOTHESIS** — under active investigation, not yet confirmed:
> That a ground-truth-free peer-agreement statistic (`loo_agreement`) can identify the degraded replica above chance, making it usable as a production detector for a failure mode that is currently invisible. That this failure mode generalizes beyond this one implementation — now partly moved to ESTABLISHED for the replica-level `index_recall` axis on Qdrant, still a hypothesis for the detector, for systems with real anti-entropy, and for the mechanism. ~~That the per-replica `index_recall` loss on Qdrant does not fully heal after chaos stops~~ — **measured and retired 2026-09-04**: at 180s it heals (see ESTABLISHED). The 50s reading that suggested otherwise was a horizon effect, on 4–5 samples. What remains hypothesis-level from that experiment: that Qdrant's *data* healing is horizon-dependent rather than seed-inconsistent — 4 of 4 seeds recovered 100% of missing ids at 180s where PR #6's 50s window saw 0–100%, on runs not designed to test it. One specific objection to the detector has now been tested and did not reproduce: its above-chance performance does **not** appear to be an artifact of the harness's pinned, seeded query set. Three 5-seed conditions — pinned, non-pinned at 100 queries/round, non-pinned at 15 — gave mean hit rates of 0.87 / 0.86 / 0.81 against a 1/3 chance line, with per-seed values spanning 0.65–1.00 across the three conditions and overlapping heavily between them, and all nine pairwise between-condition comparisons non-significant (p = 0.15–0.90); see [`research/loo_agreement_nonpinned_queries/SPEC.md`](research/loo_agreement_nonpinned_queries/SPEC.md). Tested on SIFT1M, this topology, at 15–100 queries per round — and at 5 seeds per condition the test is a weak instrument, so this weakens that confound rather than eliminating it. It does not by itself move this out of HYPOTHESIS, which still rests on one system and one implementation.

**OPEN** — unresolved questions this repo does not answer:
> The root cause of why `index_recall` degrades under chaos. A dedicated forensic tool (`graph_forensics.py`) found no average difference in neighbour-list quality between baseline and chaos replicas — except one replica, never itself killed, that lost reachability to 58.7% of its own graph while every structural check on it looked clean. Two specific hypotheses were tested and ruled out with clean reproductions; the actual mechanism is still unknown. Full writeup: [`docs/postmortems/catastrophic-disconnection.md`](docs/postmortems/catastrophic-disconnection.md). Whether the divergence effect scales with corpus size is also untested.

**DO NOT CLAIM** — statements this evidence does not support:
> "Approximate indexes have no observable correctness criterion under replication" as a general claim (true as a motivating intuition, unproven beyond n=1 system). "Vector databases silently lose data" in general (Milvus #37703 shows a genuinely *loud* failure — the honest claim is that approximation *permits* silence, not that it's universal). "No vector DB repairs missing data" (Weaviate/Vespa do, at the object level — the gap is that object-level repair cannot see graph-level damage). "We understand why recall degrades" (mechanism is open, see above). Anything implying this generalizes to Milvus, Weaviate, or production deployments — untested; for Qdrant, what is established is the replica-level `index_recall` divergence, the data-completeness divergence, and the healing transient at a 180s horizon — all on one host at 100k vectors, and each with the qualifiers stated where it is claimed. ~~"Qdrant's ANN graph resists chaos" — or that it differs from nano-db on that axis in either direction: the `index_recall` null behind that reading was measured over a mostly- or entirely-unindexed corpus, which serves exact scans, so it is an absent measurement rather than a null result.~~ — superseded 2026-09-04: measured on an indexed corpus, Qdrant's graph *does* diverge at the replica level (PR #31). What must still not be claimed: **"Qdrant's `index_recall` diverges under chaos" without the unit** — at the cluster level (mean over six replicas) it is a null at p = 0.31, and the two statements are both true. "We know what a kill does to Qdrant's HNSW" — the loss localizes to the killed replica; the mechanism (WAL-replayed points in a fresh segment, changed entry points, completeness loss reshaping the local ground truth) is not observed. "Qdrant's graph damage persists" — measured at 180s, it does not; the 50s reading was a horizon effect. Nor the reverse without its qualifiers: **"Qdrant heals" is a claim about 4 of 5 seeds (one unmeasured), at the replica level, within 180s, on one host at 100k vectors, on a metric with ~1% of headroom whose closest seed clears baseline by 0.0002.** "Qdrant repairs graph damage where nano-db does not" as an architectural statement — the two systems were observed on different horizons and axes; that comparison has not been run. "`loo_agreement` is robust to realistic query workloads" as a general claim — what was tested is query *pinning* and per-round query *count* (15–100), both drawn from SIFT1M's own query distribution; a workload differing in distribution (a different embedding distribution, adversarially hard queries) is untested and is a larger question than the one that was closed.

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

Two systems now, but only one of them from scratch and only one axis each way: the replica-level `index_recall` and completeness divergence reproduce on Qdrant; the detector and the mechanism remain nano-db-only or open, and the healing question is answered on Qdrant only (at a 180s horizon, 4 of 5 seeds) while nano-db's own "missing data has not returned" was observed on shorter windows — the single biggest open question is still whether any of this holds on a system with real anti-entropy. 5 seeds, which sits at the exact statistical floor for the rank test used (p = 0.0079 is the smallest attainable value at n=5, so it indicates the groups separate completely rather than that the effect is large). Ground truth is brute-force, practical only to ~10⁵–10⁶ vectors — a mechanism study, not a scale study. The gated Qdrant protocol has a duration limit of its own: at 240–250s and ~1.6k writes/s the un-indexed appendable tail keeps every replica near the 0.95 conditioning bar, so long runs retain as few as 29% of their rounds (see [`research/qdrant_index_recall_healing/`](research/qdrant_index_recall_healing/)). `chaos_harness.py` uses SIGKILL, which does not lose dirty mmap pages — machine-level crash consistency is a separate, unaddressed gap. Full list: the "Known limits" section of [`research/replica_recall/README.md`](research/replica_recall/README.md#known-limits).

## Open research questions / next experiments

1. **Cross-system replication** (highest priority) — the Qdrant leg is done on both axes. A 5-seed sweep ([`research/cross_system_replication/`](research/cross_system_replication/)) established `completeness`/`e2e_recall` divergence; the graph-quality axis, first mis-measured over an un-indexed corpus ([`research/qdrant_optimizer_masking/`](research/qdrant_optimizer_masking/)), was re-measured with the corpus gated indexed ([`research/qdrant_index_gate/`](research/qdrant_index_gate/)) and shows replica-level `index_recall` divergence at p = 0.0079 with a cluster-level null ([`research/qdrant_gated_index_recall/`](research/qdrant_gated_index_recall/)). The healing question is answered too ([`research/qdrant_index_recall_healing/`](research/qdrant_index_recall_healing/)): the loss is a transient of the restart at a 180s horizon. **The remaining step is a system with real anti-entropy (Weaviate)** — which is where the field-level claim in `research/RELATED_WORK.md` actually gets tested, since Qdrant, like nano-db, has no graph-level repair to observe.
2. **Root-cause closure** on the 58.7%-loss anomaly.
3. **Larger seed count or bootstrap confidence intervals**, beyond the n=5 statistical floor.
4. **Scale sensitivity** beyond the current brute-force ground-truth cap.
5. **Detector robustness against a different query *distribution*** — pinning and per-round query count have now been tested (pinned vs. non-pinned, and 100 vs. 15 queries per round) with no difference detectable at 5 seeds per condition, so "detection is an artifact of a pinned workload" no longer stands unaddressed (see HYPOTHESIS above). Two query counts are not the whole axis, though, and what remains untested is a query workload drawn from a genuinely different distribution than the corpus's own — or an adversarial one.

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

### Quick start

```bash
git clone --recurse-submodules https://github.com/shlokkvaishnav/Replica-Recall-Divergence.git
cd Replica-Recall-Divergence
./demo/cluster.sh up          # boots a 2-shard x 3-replica cluster + Grafana
python3 demo/demo_chaos.py    # kills the Raft leader mid-write, verifies zero writes dropped
./demo/cluster.sh chaos       # 60s of continuous random process kills, invariant report at the end
```

![Cluster architecture](docs/architecture/images/architecture.png)

Three Raft coordinators handle write/failover consensus; each of 2 shards has 3 replicas behind consistent hashing, with quorum writes and epoch-fenced failover. Three invariants are checked continuously: no confirmed write disappears, no split-brain, full recovery after chaos stops — a stronger but narrower guarantee than what `research/replica_recall/` measures above (write survival at the cluster level, not per-replica search *quality*).

| Metric | Value |
|--------|-------|
| Cluster insert throughput (Docker, 4 clients) | 146 vec/s |
| Search latency p50 / p99 (Docker, 167k vectors) | 5.9 ms / 27.9 ms |
| Failover recovery | 0.5 s |
| Raft leader election | < 1 s |
| Recall@10 (synthetic data — see caveat) | ≤ 81.6%, `ef_search`-dependent |

These numbers are mostly un-re-measured since first written and largely run on synthetic data, which is why `research/replica_recall/` re-measures recall on real SIFT1M instead — see [`docs/postmortems/recall-bugs.md`](docs/postmortems/recall-bugs.md) for what synthetic-data numbers hid on this project specifically. Full table, footnotes, benchmark methodology, and tail-latency scaling model: [`docs/architecture/INTERNALS.md`](docs/architecture/INTERNALS.md).

### Building & testing

```bash
mkdir build && cd build
cmake .. -DCMAKE_BUILD_TYPE=Release -DNANODB_BUILD_CLUSTER=ON
cmake --build . -j$(nproc)
ctest --output-on-failure   # 9 unit tests
```

Requires CMake 3.16+, g++ 13+, `protobuf-compiler`, `libgrpc++-dev`, `libomp-dev` (Docker builds need none of this locally). `research/replica_recall/test_metrics.py` separately validates the measurement core the research findings depend on (75 checks, no cluster needed). Full internals — mmap storage layout, HNSW graph, SIMD kernels, Raft leader election and the Figure 8 commit rule, observability: [`docs/architecture/INTERNALS.md`](docs/architecture/INTERNALS.md).
