# Replica recall divergence under failure

**Layer 1 experiment.** Does the recall of an approximate index diverge across
replicas under failure — and can you detect it without ground truth?

## The question

Every streaming-ANN result (FreshDiskANN, the NeurIPS'23 Big-ANN streaming
track, Mycelium, LSM-VEC) studies **one index on one machine**. Every
distributed-correctness tool (Jepsen/Knossos/Elle) assumes operations have
**exact expected values**. Nobody works the intersection, and the intersection
has a nasty property:

> An approximate index has no observable correctness criterion under replication.

A replica returns *k* neighbours. They look fine. You cannot distinguish:

| cause | what the client sees |
|---|---|
| healthy replica, ordinary ANN nondeterminism | k plausible results |
| replica silently missing 30% of its vectors | k plausible results |
| replica whose graph degraded from churn | k plausible results |
| replica stale w.r.t. recent writes | k plausible results |

No crash, no error, no consistency violation any existing checker can name.
Practitioners report this as real — "replica recall variance … quiet to
catastrophic instantly" — with no published study.

## What is measured

Four numbers per replica per sample. The first three need ground truth; the
fourth does not, and that asymmetry is the point.

| metric | ground truth used | isolates |
|---|---|---|
| `index_recall` | exact top-k over **the replica's own live set** | graph / ANN quality |
| `completeness` | intended set (no search involved) | data content |
| `e2e_recall` | exact top-k over **the intended set** | what a client experiences |
| `shard_agreement` | **none** — pairwise overlap across a shard | shard-level health |
| `loo_agreement` | **none** — one replica's overlap with its peers | *which replica to distrust* |

Holding data content constant (`index_recall`) versus holding search constant
(`completeness`) is what separates "the graph rotted" from "the data is
missing". A single recall number cannot do this, which is why nobody has
reported the distinction.

The last two are the only ones computable on a live system, and `loo_agreement`
is the one that matters. Correlating shard-level agreement against shard-level
recall turns out to be close to tautological: when every replica misses the
*same* hard queries, cross-replica overlap collapses onto recall whether or not
anything is broken — it reads ~1.0 on a healthy cluster too, so it detects
nothing.

The operational question is not "is recall low" but **"which replica should I
distrust?"** So each replica is scored by how well it agrees with its peers,
and the experiment asks whether the lowest-scoring replica is in fact the
lowest-recall one, measured against a chance baseline of 1/n. That is a real
detection test. If it beats chance, it is a production signal for a currently
silent failure (Layer 3). If it does not, that is also a result — it means
detection requires sentinel queries with known answers, not peer comparison.

## Design decisions worth knowing

**Probes bypass the coordinator.** Every call goes directly to a replica's
gRPC port via `ShardService.Search` / `ListLocalIds`. Going through the
coordinator would merge replicas via scatter-gather and hide the exact
divergence under study.

**The intended set is derived empirically, not from routing.** A replica of
shard 0 legitimately does not hold shard 1's vectors, so "intended" must be
per-shard. Rather than reimplement the consistent-hash ring in Python (which
would silently drift from the C++), it is defined as:

```
intended(s) = (union of live ids across replicas of s) ∩ (confirmed, settled writes)
```

This measures divergence *within* a replica group. The case it deliberately
does not catch — a confirmed id that **every** replica of a shard lost — is
total data loss, a different failure, already covered by `chaos_harness.py`'s
invariant #1 via the coordinator.

**A settling window is applied.** A write confirmed 200 ms ago may legitimately
not have reached every replica. Counting it would score normal replication lag
as data loss. Only writes confirmed more than `--settle-s` ago (default 2 s)
are held against a replica.

**The query set is pinned and seeded.** Identical at every sample and across
runs, so recall differences are attributable to the cluster, not the queries.

**Unreachable is recorded, not raised.** The chaos loop is killing these
processes. An unreachable replica is a data point — and notably the *honest*
failure mode, the one you can actually see.

**Partial samples are discarded.** If any query in a sweep fails, the replica
is marked unreachable for that sample rather than scored on a partial result,
which would mix pre- and post-failure state inside one measurement.

## Running it

Requires Linux with the cluster binaries built (the harness launches processes
directly; no Docker).

```bash
pip install grpcio grpcio-tools numpy
cmake -B build -DCMAKE_BUILD_TYPE=Release -DNANODB_BUILD_CLUSTER=ON
cmake --build build -j$(nproc)

# baseline first: divergence with NO faults. Establishes the noise floor.
python research/replica_recall/run_experiment.py --duration 180 --no-chaos
mv research/replica_recall/results research/replica_recall/results_baseline

# then with fault injection
python research/replica_recall/run_experiment.py --duration 300

python research/replica_recall/analyze.py
python research/replica_recall/analyze.py --results-dir research/replica_recall/results_baseline
```

**Run the baseline first.** Some cross-replica disagreement is expected even
in a healthy cluster: HNSW insertion order differs per replica, so the graphs
are genuinely different. Without the no-chaos noise floor you cannot claim any
observed divergence was caused by failure.

## Validating the measurement core

The metric math is pure and tested without a cluster:

```bash
python research/replica_recall/test_metrics.py
```

The load-bearing case is `test_decomposition_separates_causes`, which builds
replicas broken in *different* ways and asserts the metrics finger the right
culprit — a replica with 70% of the data but perfect search must show
`index_recall ≈ 1.0, completeness = 0.7`, while a replica with all the data
and a bad graph must show the opposite. If that fails, every number the
experiment produces is uninterpretable.

## Files

| file | role |
|---|---|
| `metrics.py` | measurement core — pure functions, no I/O |
| `test_metrics.py` | offline validation, no cluster needed |
| `probe.py` | direct per-replica gRPC client |
| `run_experiment.py` | orchestration; reuses `chaos_harness.py` for process management and fault injection |
| `analyze.py` | Q1–Q4 from `samples.csv` |

## Interpreting the output

`analyze.py` answers five questions:

- **Q0** — does recall drift over the run on its own? Q2 compares an early
  "before any kill" bucket against later ones, and early samples come from a
  smaller index. If recall declines with index growth in the **baseline**,
  Q2's gap is confounded and cannot be read as a failure effect. Always read
  Q0 on the baseline before trusting Q2.
- **Q1** — spread of `e2e_recall` across replicas of one shard at one instant.
  Non-trivial spread means two replicas answered the same queries differently
  at the same moment, and the client saw only one of them.
- **Q2** — metrics bucketed by time since the nearest kill. Recovery means
  later buckets return to the pre-kill level; a permanent step down is the
  interesting result — subject to Q0.
- **Q3** — `index_recall` vs `completeness` on the worst samples. This is the
  decomposition, and the part no existing tool reports.
- **Q4** — can `loo_agreement` pick out the degraded replica, versus chance?
  The Layer 3 test. **Compare against the baseline**: a hit rate just as high
  with no faults is measuring noise, not detection.

## Known limits

- Ground truth is brute force over the retained vector set, so this is
  practical to roughly 10⁵–10⁶ vectors on one host. It is a mechanism study,
  not a scale study.
- Both metrics that need ground truth depend on the writer retaining every
  vector it confirmed, so the experiment must drive the writes; it cannot be
  pointed at a pre-existing cluster with unknown contents.
- `chaos_harness.py` kills processes with SIGKILL, which does not lose dirty
  mmap pages. Machine-level crash consistency is a separate, unaddressed gap
  (see the durability note in the project roadmap).
- Only two shards × three replicas by default, inherited from
  `chaos_harness.py`'s topology.
