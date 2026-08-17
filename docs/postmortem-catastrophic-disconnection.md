# One replica lost 58.7% of its own graph. It was never killed.

This is a postmortem of an open investigation, not a closed one. Two specific,
testable hypotheses got formed and ruled out with clean evidence. The actual
cause is still unknown. I'm writing it down anyway, because the ruled-out list
is itself the useful part — and because burying a negative result is how the
same wrong guess gets made twice.

## The question this was trying to answer

The [replica-recall research](../research/replica_recall/README.md) had already
established *that* node-kill chaos degrades `index_recall` — search quality
measured with the data held constant — separately from data loss. That's a
real, replicated finding (`0.9965 → 0.9588` on real SIFT1M, p at the exact
statistical floor for 5 seeds). What it didn't answer was *why*.

The working hypothesis was mundane and plausible: a replica that misses some
writes while it's down links its later insertions into a sparser graph than a
replica with the full corpus available, and — because HNSW never revisits or
repairs old adjacency lists — that damage is permanent. Semantic degradation,
gradual, proportional to how much was missed.

## The instrument

`research/replica_recall/graph_forensics.py` reads a shard's `index.ndb` file
directly off disk — it's a dense array of fixed-size nodes behind a small
header, so no running cluster is needed to inspect it. It reports structural
properties (degree, dangling edges, in-degree, reachability from the entry
point via BFS) and a semantic one: `link_quality`, which scores a sampled
node's stored neighbours against the *exact* nearest neighbours among the
vectors that replica currently holds. Ground truth comes from the replica's
own contents, so it isolates graph quality from data completeness the same
way `index_recall` does.

First lesson, before any real result: **never read a live cluster.** The
`element_count` header field lags the actual node writes by design (the node
body is persisted before the counter is bumped), so a mid-write snapshot looks
exactly like corruption. The first attempt at this measured a running cluster
and reported 168 dangling edges that turned out to be nothing — the fix was a
paired driver (`forensics_experiment.py`) that always tears the cluster down
before reading its files.

## Result 1: the semantic hypothesis is wrong

Averaged across 30 chaos replicas (5 seeds), `link_quality` under chaos
(0.4558) was not distinguishable from baseline (0.4517) — if anything
marginally higher, well within noise. Per-seed deltas went both directions.
**Chaos does not, on average, degrade neighbour-list quality.**

## Result 2: one replica, wildly different from the other 29

The per-replica distribution told a different story than the mean. 20 of 30
chaos replicas showed zero reachability damage — identical to baseline. One
replica (`shard-1-1`, one specific seed) did not:

- **58.7% of its graph (12,892 of 21,957 nodes) unreachable from the entry
  point.** Real data, sitting on disk, that no search could ever find.
- Every other structural check on that same replica was clean: no dangling
  edges, no self-loops, normal degree distribution. The unreachable nodes all
  had real in-edges from each other — a well-formed, internally-connected
  subgraph, just severed from the component containing the entry point.
- Its `link_quality` (0.307) was the single worst of all 60 replicas measured
  across both conditions — consistent with something specifically wrong with
  this replica, not sampling noise.
- **It was never killed.** `kill_count = 0` for the entire run, cross-checked
  against the chaos event log. Of 90 replicas that *were* killed at least
  once (some up to 9 times) in the follow-up batch, **zero** showed any
  comparable damage.

That last fact eliminates the obvious story. Whatever happened, it happened to
a bystander.

## Hypothesis A: pure concurrency

If this were a race condition in the insert path itself — two threads
tripping over each other's state — more concurrent pressure should make it
easier to trigger, not impossible.

**Test:** 16 concurrent writer threads (4× the normal chaos-run load), zero
chaos, zero process kills, ~40,000 vectors confirmed across 6 replicas.

**Result:** every replica came back completely clean. Zero unreachable nodes,
zero isolated nodes, uniform link quality. Heavier concurrency than the
original chaos runs used, and nothing broke.

**Verdict: ruled out.** This is not a plain concurrency bug in `insert()`.

## Hypothesis B: the duplicate-insert bug

Reading `cluster/shard_service_impl.hpp`'s `Insert` handler found a real
defect, independent of the mechanism search:

```cpp
auto [local_id, is_new] = id_map_.assign(request->external_id());
(void)is_new;
index_.insert(vec, local_id, request->metadata());
```

`is_new` — whether this `external_id` was already known on this shard — is
computed correctly and then explicitly discarded. Every insert proceeds
unconditionally, first time or tenth.

Tracing `HNSW::insert()` for what happens if the same node gets inserted
twice: `*node_ptr = new_node` unconditionally overwrites the node, including
wiping every one of its neighbour links (the `Node` constructor memsets them
to the empty-fill value). If the overwritten node happened to be the entry
point, the consequence looked, on paper, severe: the re-link pass searches
for new neighbours *starting from the entry point*, which now has zero edges
— nothing to discover, nothing to link to.

That prediction turned out to be incomplete. `search_layer()` always includes
its starting node as a fallback candidate in the result set, even with zero
explored neighbours (`found_results.push(start_node)` before the exploration
loop even runs). So the very next insert after a wipe still finds the
wiped node as a valid one-candidate result, links back to it, and the entry
point regains real edges within a single subsequent insert. The bug is real —
it silently overwrites an existing vector's data with no warning, which is a
genuine correctness problem on its own — but it self-heals structurally too
fast to explain 58.7% permanent loss.

**Test, to check the prediction rather than trust it:** a deterministic,
single-threaded, zero-chaos reproduction — insert one vector, immediately
re-insert the same external_id (guaranteed to hit the entry point, since it's
the only node in the graph), then insert 5,000 more vectors normally.

**Result:** clean across all 6 replicas. Zero unreachable, zero isolated
nodes. The entry point had migrated away from the duplicated node multiple
times by the end, exactly as expected, and nothing was ever left behind.

**Verdict: ruled out.** The bug is real and worth fixing on its own merits —
silently overwriting a vector's stored data on a duplicate insert is a
correctness problem regardless of whether it explains this — but it is not
the mechanism.

## Where this leaves it

Two specific, testable hypotheses, formed from actually reading the code
rather than guessing, both built into clean deterministic reproductions, both
ruled out. What's confirmed:

- Not average/gradual — a rare (1-in-20-seeds so far), severe, all-or-nothing
  event
- Not caused by the affected replica being killed
- Not explained by concurrent-insert races in isolation
- Not explained by the duplicate-insert data-overwrite bug, despite that bug
  being real
- Structurally invisible to every check except explicit reachability BFS —
  degree distribution, dangling-edge counts, and self-loop counts all look
  completely normal on the damaged replica

What's still open: the actual trigger. The next most likely direction is
something specific to coordinator failover or peer instability — a burst of
retried or re-routed writes hitting a healthy replica differently than
steady-state load does — but that is a hypothesis, not a finding, and it
hasn't been tested yet.

## The general lesson, again

This is the same shape as
[the recall postmortem](postmortem-recall-bugs.md): a bug that every existing
check passed straight through. Structural graph checks — the kind any
"is my index healthy" monitor would plausibly run — see nothing wrong on the
damaged replica. Only asking "can I actually reach every node from the entry
point" catches it. An index can be internally well-formed, have healthy
degree statistics, and still be functionally missing most of itself.
