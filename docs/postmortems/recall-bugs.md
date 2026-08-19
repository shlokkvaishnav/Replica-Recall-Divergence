# My benchmark said 95% recall. Reality said 46%.

Both numbers came from the same code, on the same machine, on the same day.

I have been building [Nano-DB](https://github.com/shlokkvaishnav/Nano-DB), a
Raft-replicated vector database written from scratch in C++17 — custom HNSW
index, custom consensus, custom replication. It has a unit test suite, a
recall benchmark, and a chaos harness that kills processes at random and
checks that no confirmed write is ever lost. All of it passed.

Then I built a measurement harness for an unrelated research question, pointed
it at a live cluster, and the first number it printed was `recall@10 = 0.4601`
with **zero faults injected** and every replica holding **100% of the data**.

Some individual queries returned `0.0000` — a replica holding every vector,
returning none of the true top ten.

This is what was wrong, why every existing test said it was fine, and what I
think the general lesson is.

---

## Why the benchmark couldn't see it

`benchmarks/research/benchmark_recall.cpp` builds an index, sweeps `ef_search`, and
measures recall@10 against brute-force ground truth. That is the right shape
for a recall benchmark. It reported ~95%.

The measurement harness reported 46% on a shard holding ~21,000 vectors.

The benchmark was not lying, and neither was the harness. **All three recall
bugs were scale-dependent**, and the benchmark ran below the scale at which
any of them becomes visible. That turns out to be the whole story, and it is
the part worth generalizing.

---

## Bug 1: the index linked every node to its worst neighbours

HNSW inserts a node by searching for nearby candidates and linking to the
best `M` of them. The search returns candidates in a `std::priority_queue`:

```cpp
std::priority_queue<Result> candidates = search_layer(curr_obj, vec, EF_CONSTRUCTION, l);

std::vector<id_t> selected_neighbors;
while (!candidates.empty() && selected_neighbors.size() < config::M) {
    selected_neighbors.push_back(candidates.top().id);
    candidates.pop();
}
```

`std::priority_queue` with the default comparator is a **max-heap**. `Result`
orders by distance. So `top()` is the *farthest* candidate, not the nearest.

`search_layer` returns the `ef = 200` nearest candidates as a bounded
max-heap. Draining it directly and taking the first 16 selects roughly ranks
185–200 out of 200 — **the worst available neighbours, every time**, for every
node, at every layer. The line immediately after then used the worst of those
as the entry point for the next layer down.

The same file's `search()` function got this right. It drains the queue and
calls `std::reverse` before truncating to `k`. `insert()` never did.

Why the benchmark missed it: at small `N`, layer 0 allows `M_MAX0 = 32`
neighbours per node, and with only a few thousand nodes the graph is dense
enough that "the worst 16 of the nearest 200" still leaves you connected to
most of the neighbourhood. The graph is bad but navigable. It stops being
navigable as `N` grows.

---

## Bug 2: every node past ID 10,000 had no edges at all

This one is worse, and it is the reason for the 46%.

```cpp
size_t offset = HEADER_SIZE + (size_t)id * sizeof(Node);
if (offset + sizeof(Node) > storage_.get_size()) {
    std::lock_guard<std::mutex> lock(global_resize_lock_);
    if (offset + sizeof(Node) > storage_.get_size()) {
        storage_.resize(storage_.get_size() + 10 * 1024 * 1024);
        if (id >= node_locks_.size()) {
            // grow the per-node lock array
        }
    }
}
```

The lock array grows **inside the storage-resize branch**. Storage growth and
lock coverage are independent concerns, and nesting them means the lock array
only ever grows when the file happens to need more space.

The shard node pre-allocates 100 MB at startup — room for about 94,000 nodes.
So the resize never fires. So `node_locks_` stays at its initial size of
10,000, forever.

And `add_link`, the function that creates every edge in the graph, began:

```cpp
void add_link(id_t src, id_t dest, int layer) {
    if (src >= node_locks_.size()) return;   // silently does nothing
    ...
}
```

**Every node with an ID at or above 10,000 was inserted with zero neighbours.**
Unreachable by any search, forever, with no error, no log line, and no failing
test. On a shard holding 21,000 vectors, more than half the index was orphaned.

Why the benchmark missed it: it never inserted 10,000 vectors.

The fix was to stop growing the array at all. `node_locks_` is now a fixed
4,096-entry striped pool indexed by `id % LOCK_STRIPES`. It cannot fall
behind, and it removes a second latent bug — growing a `std::vector` while
other threads index into it is a reallocation race. `add_link` holds one lock
at a time and never nests, so stripe collisions cannot deadlock.

---

## Bug 3: neighbour pruning stripped out every long-range link

When a node's neighbour list is full, something has to be evicted. The
original code kept whichever neighbours were closest:

```cpp
// Replace the farthest neighbor if the new one is closer
if (dest_dist < max_d && max_idx != -1) {
    node->neighbors[layer][max_idx] = dest;
}
```

This is intuitive and it is wrong. HNSW's navigability comes from long-range
links — the shortcuts that let greedy search cross the graph in a few hops.
Keeping only the closest neighbours evicts precisely those shortcuts, every
time a nearer node is inserted beside an established one. Neighbourhoods
become tight local clusters, and greedy search can no longer traverse between
them.

The HNSW paper specifies a different rule (Algorithm 4): keep a candidate only
if it is closer to the base element than it is to any already-selected
neighbour. That admits a distant candidate covering a direction nothing else
covers, and rejects a near one that merely duplicates an existing link. It is
what `hnswlib`'s `getNeighborsByHeuristic2` implements, and I had skipped it
as an optimization detail. It is not an optimization detail.

This one degrades continuously with `N`, which is why it never showed up as a
threshold effect — just a benchmark run at a size where it hadn't bitten yet.

---

## Two data races, for completeness

`get_random_level()` shared a single `std::mt19937` across concurrent
`insert()` calls. `std::mt19937` is not thread-safe; this is a straightforward
data race, and it is now `thread_local`.

`entry_point_id_` and `current_max_layer_` were updated without
synchronization:

```cpp
if (level > current_max_layer_) {
    entry_point_id_ = id;
    current_max_layer_ = level;
}
```

Two threads inserting high-level nodes concurrently can both pass the check,
and interleave, leaving the entry point claiming a layer it has no links on —
which strands every subsequent search descending from it. Now double-checked
under a mutex with both fields atomic.

The effect was measurable: at matched index size, four concurrent writers
reached materially lower recall than a single writer.

---

## The numbers

Single-threaded, uniform random 128-dimensional vectors, `ef_search = 100`,
recall@10 against exact brute force. "Before" is the code with only an earlier
bounds-checking fix applied:

| vectors | before | after |
|--------:|-------:|------:|
|   2,000 |  0.876 | 0.981 |
|   5,000 |  0.732 | 0.919 |
|  10,000 |  0.596 | 0.820 |
|  20,000 |  0.122 | 0.715 |
|  40,000 |  0.045 | 0.592 |

The cliff between 10,000 and 20,000 in the "before" column is bug 2 becoming
load-bearing. On the live cluster, mean `index_recall` went from 0.4601 to
0.8387 under identical conditions.

---

## What I actually take from this

**Small-scale tests systematically miss scale-dependent correctness bugs in
graph indexes.** This is not a claim that my tests were bad — they were the
usual ones, and they were all green. It is that a graph index has failure
modes which are *invisible below a threshold* and *continuous above it*, and a
benchmark that runs at 1,000 vectors is structurally incapable of seeing
either. Every one of the three recall bugs was of that kind.

**"Silently does nothing" is the most expensive line of code you can write.**
`if (src >= node_locks_.size()) return;` was defensive programming. It turned
a crash — which I would have found in minutes — into a silent halving of index
quality that survived months and a full test suite.

**Approximate systems hide their own bugs.** This is the part I find most
interesting, and it is what the research harness exists to study. An exact
data structure that loses half its contents throws, or returns nothing, or
fails a checksum. An approximate index that loses half its contents returns
ten plausible-looking results and reports success. There is no assertion to
violate. The only way to catch it is to compare against ground truth you
computed independently — which is exactly what a recall benchmark does, and
exactly why running one at a realistic scale matters more than the number of
unit tests you have.

**The bugs were found by a tool built for something else.** I was not looking
for index bugs. I was building a harness to measure whether replicas of an
approximate index diverge under failure, which required computing exact
ground truth per replica. The first run of that harness found all of this. The
lesson I draw is not "measure more" but "measure the thing you are actually
claiming" — I claimed 95% recall, and I had never measured it under conditions
resembling use.

---

## Reproducing

```bash
git clone --recurse-submodules https://github.com/shlokkvaishnav/Nano-DB.git
cd Nano-DB
cmake -B build -DCMAKE_BUILD_TYPE=Release -DNANODB_BUILD_CLUSTER=ON
cmake --build build -j$(nproc)
ctest --test-dir build --output-on-failure
```

The measurement harness, the metric definitions, and the reasoning behind them
are in `research/replica_recall/`. The fixes are in `include/index/hnsw.hpp`,
in three commits, each with the measurement that justified it.

## The decay that wasn't a bug

After those fixes recall still fell with index size — 0.98 at 2k down to 0.73
at 20k — and a correct HNSW should be flatter. I had two suspects: the
hierarchy (`MAX_LAYERS` capped at 4 with a 0.03 level probability, leaving
only a few dozen nodes above layer 1) and the hardcoded `ef_search = 100`.
There was a third possibility I thought less likely: that it was not a defect
at all, but distance concentration in uniform random data.

I tested all three rather than guessing, each as a patched build measured
against exact brute force. Recall@10 at 20,000 vectors:

| variant | recall@10 |
|---|---|
| baseline (uniform data, `MAX_LAYERS=4`, p=0.03, ef=100) | 0.726 |
| `MAX_LAYERS=8` | 0.731 |
| level probability 0.0625 (the standard 1/ln(M)) | 0.733 |
| both | 0.726 |
| ef_search 200 | 0.876 |
| ef_search 400 | 0.949 |
| **same index, low-intrinsic-dimension data** | **1.000** |

Two things fell out.

**The hierarchy hypothesis was wrong.** Every variant landed within noise of
the baseline. Had I "fixed" it on intuition I would have changed the node
layout, invalidated every existing index file, and improved nothing.

**The decay was the data, not the index.** On data with low intrinsic
dimensionality — the regime real embeddings occupy — recall is **1.000 at
every size tested**, with no code change at all. Uniform random 128-dimensional
points suffer distance concentration: the ratio of nearest to farthest
neighbour tends toward 1, so the "true top 10" is a near-arbitrary pick from a
crowd of near-equidistant candidates, and it gets worse as `N` grows. Recall
falling with `N` on that data is expected behaviour, not a defect.

The one real gap the exercise did surface is that `ef_search` was hardcoded,
which put the recall/latency tradeoff out of reach from outside. It is a
parameter now. That is not a bug fix — it is the knob that was missing.

The general lesson is the one I keep relearning: **a benchmark on synthetic
data measures your benchmark as much as your system.** I nearly spent a day
fixing a hierarchy that was never broken, on evidence that was an artifact of
how I generated vectors.
