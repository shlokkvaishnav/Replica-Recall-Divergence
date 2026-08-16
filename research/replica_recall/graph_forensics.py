"""
Autopsy of an HNSW index file. Answers *why* a damaged replica searches worse.

The replica-recall experiment establishes that node-kill chaos drops
index_recall with the data held constant -- a replica missing nothing still
searches worse, so the graph is damaged independently of its contents. That is
the finding. This is the follow-up question: what, structurally, is the damage?

The index is memory-mapped to disk as a dense array of fixed-size nodes, so a
damaged replica can be dissected offline with no cluster running:

    HEADER_SIZE (64) + id * sizeof(Node) (1056)

The hypothesis this is built to test: insertion writes the node first and links
it afterwards, so a SIGKILL in between leaves a node that is *present* but that
nothing points at. Such a node is invisible to search however many times it is
queried, and no amount of data-level repair would fix it -- the vector is not
missing. That would explain the whole finding.

The load-bearing metric is therefore in-degree, not out-degree. A node with no
in-edges and which is not the entry point cannot be reached by any traversal
from any starting point. That is a proof, not an estimate: it needs no
assumption about how search descends the layers.

    python research/replica_recall/graph_forensics.py chaos_run/data/shard-0-0
    python research/replica_recall/graph_forensics.py chaos_run/data --compare
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np

# Verified against the compiled headers with offsetof/sizeof rather than
# derived by hand -- see include/index/graph_node.hpp and hnsw.hpp.
HEADER_SIZE = 64
MAX_LAYERS = 4
M_MAX0 = 32
VECTOR_DIM = 128
NANODB_MAGIC = 0x4E444200
NO_NEIGHBOR = 0xFFFFFFFF          # neighbors are memset to -1 at construction

NODE_DTYPE = np.dtype([
    ("id", "<u4"),                             # @0
    ("max_layer", "<i4"),                      # @4
    ("is_deleted", "u1"),                      # @8
    ("_pad0", "V3"),                           # @9   (float alignment)
    ("vector", "<f4", VECTOR_DIM),             # @12
    ("neighbors", "<u4", (MAX_LAYERS, M_MAX0)),  # @524
    ("neighbor_counts", "<i4", MAX_LAYERS),    # @1036
    ("_pad1", "V4"),                           # @1052 (alignas(32) tail)
])
assert NODE_DTYPE.itemsize == 1056, NODE_DTYPE.itemsize

HEADER_DTYPE = np.dtype([
    ("magic", "<u4"),
    ("element_count", "<u4"),
    ("entry_point_id", "<i4"),
    ("max_layer", "<i4"),
    ("reserved", "V48"),
])
assert HEADER_DTYPE.itemsize == HEADER_SIZE


def load_index(path: str):
    """mmap an index.ndb and return (header, nodes) with nodes truncated to
    the live extent."""
    if os.path.isdir(path):
        path = os.path.join(path, "index.ndb")
    size = os.path.getsize(path)

    raw = np.memmap(path, dtype=np.uint8, mode="r")
    header = raw[:HEADER_SIZE].view(HEADER_DTYPE)[0]
    if int(header["magic"]) != NANODB_MAGIC:
        raise ValueError(f"{path}: bad magic 0x{int(header['magic']):08X}, "
                         f"expected 0x{NANODB_MAGIC:08X}")

    capacity = (size - HEADER_SIZE) // NODE_DTYPE.itemsize
    all_nodes = raw[HEADER_SIZE:HEADER_SIZE + capacity * NODE_DTYPE.itemsize] \
        .view(NODE_DTYPE)

    # Do NOT use element_count as the extent. add_vector writes the node at
    # hnsw.hpp:120 and only increments element_count at :207, *after* the
    # linking pass -- so a process killed mid-insert leaves a node fully
    # written to disk that the counter never acknowledges. Those nodes are
    # precisely the damage being looked for, and taking element_count as the
    # extent would exclude the entire population from the analysis.
    #
    # Scan for the real extent instead. The file is pre-allocated zero-filled,
    # and a written node has its unused neighbour slots memset to -1
    # (0xFFFFFFFF) by the Node constructor, so "any slot holds the fill value"
    # separates written rows from untouched ones. Layers above max_layer are
    # entirely fill, and most nodes live only on layer 0, so this is decisive.
    written = (all_nodes["neighbors"] == NO_NEIGHBOR).any(axis=(1, 2))
    written |= (all_nodes["vector"] != 0).any(axis=1)
    nz = np.flatnonzero(written)
    extent = int(nz[-1]) + 1 if nz.size else 0

    return header, all_nodes[:extent], capacity


def analyse(path: str) -> dict:
    header, nodes, capacity = load_index(path)
    n = len(nodes)
    entry = int(header["entry_point_id"])

    out: dict = {
        "path": path,
        "element_count": int(header["element_count"]),
        "capacity": int(capacity),
        "entry_point_id": entry,
        "header_max_layer": int(header["max_layer"]),
        "nodes_examined": n,
        # Nodes written to disk that element_count never acknowledged. The node
        # body is persisted before the counter is bumped, so a kill in that
        # window leaves exactly this: real data the index does not know it has.
        "uncounted_nodes": n - int(header["element_count"]),
    }
    if n == 0:
        return out

    counts = nodes["neighbor_counts"]         # (n, MAX_LAYERS)
    nbrs = nodes["neighbors"]                 # (n, MAX_LAYERS, M_MAX0)

    # ---- degrees -----------------------------------------------------------
    l0 = np.clip(counts[:, 0], 0, M_MAX0)
    out["deg0_mean"] = float(l0.mean())
    out["deg0_median"] = float(np.median(l0))
    out["deg0_p5"] = float(np.percentile(l0, 5))
    out["deg0_min"] = int(l0.min())

    # Out-degree zero at layer 0: written, never linked outward.
    orphan_out = int((l0 == 0).sum())
    out["out_degree_0"] = orphan_out
    out["out_degree_0_frac"] = orphan_out / n

    # ---- edge extraction ---------------------------------------------------
    # Only the first neighbor_counts[layer] slots are valid; the rest are the
    # 0xFFFFFFFF fill. Build a mask rather than filtering by sentinel, because
    # a corrupt count is exactly the kind of damage being looked for.
    slot = np.arange(M_MAX0)[None, None, :]
    valid = slot < np.clip(counts, 0, M_MAX0)[:, :, None]

    src = np.broadcast_to(np.arange(n, dtype=np.uint32)[:, None, None],
                          nbrs.shape)[valid]
    dst = nbrs[valid]

    out["edges_total"] = int(dst.size)
    # Links pointing outside the written range, or at the -1 fill despite the
    # count claiming they are valid. Either is corruption.
    dangling = (dst >= n)
    out["dangling_edges"] = int(dangling.sum())
    out["self_loops"] = int((dst == src).sum())

    good = ~dangling
    src_g, dst_g = src[good], dst[good]

    # ---- in-degree: the load-bearing metric --------------------------------
    # A node with no in-edges, that is not the entry point, cannot be reached
    # by any traversal from any start. It is present in the file, counted as
    # data, and permanently invisible to search. No assumption about layer
    # descent is required for that conclusion.
    indeg = np.bincount(dst_g.astype(np.int64), minlength=n)
    unreachable = indeg == 0
    if 0 <= entry < n:
        unreachable[entry] = False
    n_unreachable = int(unreachable.sum())
    out["in_degree_0"] = n_unreachable
    out["in_degree_0_frac"] = n_unreachable / n
    out["indeg_mean"] = float(indeg.mean())

    # Present but invisible: has data, cannot be found. The population the
    # finding predicts.
    both = int((unreachable & (l0 == 0)).sum())
    out["isolated_both_ways"] = both

    # ---- reachability from the entry point ---------------------------------
    # Directed BFS over the layer-0 graph. A superset of what search actually
    # visits (search is greedy and bounded by ef), so unreached here means
    # unreachable, full stop.
    if 0 <= entry < n:
        adj_start = np.zeros(n + 1, dtype=np.int64)
        order = np.argsort(src_g, kind="stable")
        s_sorted = src_g[order].astype(np.int64)
        d_sorted = dst_g[order].astype(np.int64)
        np.cumsum(np.bincount(s_sorted, minlength=n), out=adj_start[1:])

        seen = np.zeros(n, dtype=bool)
        seen[entry] = True
        frontier = np.array([entry], dtype=np.int64)
        while frontier.size:
            starts, ends = adj_start[frontier], adj_start[frontier + 1]
            take = ends - starts
            if not take.any():
                break
            idx = np.concatenate([np.arange(a, b) for a, b in
                                  zip(starts[take > 0], ends[take > 0])])
            nxt = np.unique(d_sorted[idx])
            nxt = nxt[~seen[nxt]]
            seen[nxt] = True
            frontier = nxt
        out["reachable_from_entry"] = int(seen.sum())
        out["unreachable_from_entry"] = int(n - seen.sum())
        out["unreachable_from_entry_frac"] = float((n - seen.sum()) / n)
    else:
        out["reachable_from_entry"] = None
        out["unreachable_from_entry"] = None
        out["unreachable_from_entry_frac"] = None

    # ---- link symmetry -----------------------------------------------------
    # add_link is called in both directions, so a one-way edge means the second
    # call did not land -- a kill mid-insert is one way to get that.
    edge = src_g.astype(np.int64) * n + dst_g.astype(np.int64)
    rev = dst_g.astype(np.int64) * n + src_g.astype(np.int64)
    mutual = np.isin(rev, edge, assume_unique=False)
    out["edges_scored"] = int(edge.size)
    out["asymmetric_edges"] = int((~mutual).sum())
    out["asymmetric_frac"] = float((~mutual).mean()) if edge.size else 0.0

    # ---- layer occupancy ---------------------------------------------------
    ml = np.clip(nodes["max_layer"], 0, MAX_LAYERS - 1)
    out["layer_hist"] = [int((ml == i).sum()) for i in range(MAX_LAYERS)]
    out["deleted"] = int(nodes["is_deleted"].sum())

    return out


def link_quality(nodes, sample: int = 2000, m: int = 16,
                 seed: int = 20260808, batch: int = 256) -> dict:
    """How good are a node's stored neighbours, versus the best it could have?

    Structural forensics can come back clean while search still degrades: the
    graph can have healthy degrees, no orphans and full reachability, and yet
    every adjacency list can point somewhere worse than it should. That is the
    damage mode a replica suffers when it misses writes -- nodes inserted while
    the replica was behind get linked into a sparser graph, and nothing ever
    revisits those links once the missing vectors arrive.

    For a sample of nodes, this computes the exact m nearest neighbours *among
    the vectors this replica actually holds*, and reports what fraction of them
    appear in the node's stored layer-0 adjacency list. Ground truth is drawn
    from the replica's own contents, exactly as index_recall does, so the score
    isolates graph quality from data completeness.

    The absolute value is not meant to reach 1.0 and a healthy index will not
    score 1.0: selection is Algorithm 4's diversity heuristic, which
    deliberately keeps some far neighbours for navigability rather than the
    closest m. Only differences between replicas holding the same data are
    interpretable.
    """
    n = len(nodes)
    if n < m + 2:
        return {}

    vecs = np.ascontiguousarray(nodes["vector"])
    counts = np.clip(nodes["neighbor_counts"][:, 0], 0, M_MAX0)
    nbrs = nodes["neighbors"][:, 0, :]

    rng = np.random.default_rng(seed)
    idx = np.sort(rng.choice(n, size=min(sample, n), replace=False))

    sq = np.einsum("ij,ij->i", vecs, vecs)
    scores = np.empty(len(idx), dtype=np.float64)

    for s in range(0, len(idx), batch):
        rows = idx[s:s + batch]
        q = vecs[rows]
        # Same expanded form the index itself uses; the omitted ||q||^2 is
        # constant per row and does not affect ranking.
        d = sq[None, :] - 2.0 * (q @ vecs.T)
        d[np.arange(len(rows)), rows] = np.inf      # exclude self
        top = np.argpartition(d, m, axis=1)[:, :m]

        for j, r in enumerate(rows):
            stored = nbrs[r, :counts[r]]
            if stored.size == 0:
                scores[s + j] = 0.0
                continue
            scores[s + j] = np.isin(top[j], stored).sum() / m

    return {
        "link_quality": float(scores.mean()),
        "link_quality_p5": float(np.percentile(scores, 5)),
        "link_quality_zero": int((scores == 0).sum()),
        "link_quality_n": int(len(idx)),
    }


FIELDS = [
    ("nodes_examined", "nodes written", "{:,}"),
    ("element_count", "element_count (header)", "{:,}"),
    ("uncounted_nodes", "written but uncounted", "{:,}"),
    ("in_degree_0", "in-degree 0 (invisible)", "{:,}"),
    ("in_degree_0_frac", "  as fraction", "{:.4%}"),
    ("unreachable_from_entry", "unreachable from entry", "{:,}"),
    ("unreachable_from_entry_frac", "  as fraction", "{:.4%}"),
    ("out_degree_0", "out-degree 0", "{:,}"),
    ("isolated_both_ways", "isolated both ways", "{:,}"),
    ("link_quality", "link quality (vs ideal)", "{:.4f}"),
    ("link_quality_p5", "  p5", "{:.4f}"),
    ("link_quality_zero", "  nodes scoring 0", "{:,}"),
    ("deg0_mean", "mean layer-0 degree", "{:.2f}"),
    ("deg0_min", "min layer-0 degree", "{:,}"),
    ("asymmetric_frac", "asymmetric edges", "{:.4%}"),
    ("dangling_edges", "dangling edges", "{:,}"),
    ("self_loops", "self loops", "{:,}"),
    ("edges_total", "edges", "{:,}"),
]


def fmt(v, spec: str) -> str:
    return "-" if v is None else spec.format(v)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("path", help="a shard data dir, an index.ndb, or (with "
                                 "--compare) a directory of shard dirs")
    ap.add_argument("--compare", action="store_true",
                    help="analyse every shard-*/ under path and tabulate. "
                         "Replicas of one shard hold the same intended data, "
                         "so differences between them are damage.")
    ap.add_argument("--json", default=None, help="also write raw results here")
    ap.add_argument("--link-quality", type=int, default=0, metavar="N",
                    help="also score N sampled nodes for neighbour-list "
                         "quality against exact ground truth drawn from the "
                         "replica's own contents. Costs a brute-force pass, "
                         "so it is off by default; 2000 is plenty.")
    args = ap.parse_args()

    targets = []
    if args.compare:
        for name in sorted(os.listdir(args.path)):
            d = os.path.join(args.path, name)
            if os.path.exists(os.path.join(d, "index.ndb")):
                targets.append(d)
        if not targets:
            print(f"no shard dirs with index.ndb under {args.path}",
                  file=sys.stderr)
            return 1
    else:
        targets = [args.path]

    results = []
    for t in targets:
        try:
            r = analyse(t)
            if args.link_quality:
                _, nodes, _ = load_index(t)
                r.update(link_quality(nodes, sample=args.link_quality))
            results.append(r)
        except Exception as e:
            print(f"  {os.path.basename(t)}: FAILED ({e})", file=sys.stderr)

    if not results:
        return 1

    names = [os.path.basename(r["path"].rstrip("/\\")) for r in results]
    w = max(len(n) for n in names + ["metric"]) + 2
    print("=" * (26 + w * len(names)))
    print("HNSW graph forensics")
    print("=" * (26 + w * len(names)))
    print(f"{'metric':<26}" + "".join(f"{n:>{w}}" for n in names))
    print("-" * (26 + w * len(names)))
    for key, label, spec in FIELDS:
        print(f"{label:<26}" +
              "".join(f"{fmt(r.get(key), spec):>{w}}" for r in results))

    print()
    print("in-degree 0 is the load-bearing number: such a node is present in")
    print("the file but no edge points at it, so no traversal can reach it")
    print("from any start. It holds data and is invisible to search, which is")
    print("damage that data-level repair would not even detect.")

    if args.json:
        with open(args.json, "w") as f:
            json.dump(results, f, indent=2)
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
