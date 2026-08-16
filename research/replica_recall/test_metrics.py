"""
Offline validation of the measurement core. No cluster required.

The load-bearing test is test_decomposition_separates_causes: it constructs
replicas that are broken in *different* ways and asserts the metrics point
at the right culprit. If that test does not hold, every number the
experiment produces is uninterpretable.

Run:  python research/replica_recall/test_metrics.py
"""

from __future__ import annotations

import sys
import numpy as np

sys.path.insert(0, __file__.rsplit("replica_recall", 1)[0] + "replica_recall")

from metrics import (                                   # noqa: E402
    Corpus, exact_topk, exact_topk_rows, recall_at_k, set_completeness,
    pairwise_agreement, leave_one_out_agreement, score_replica,
)
import sift                                             # noqa: E402
import graph_forensics as gf                            # noqa: E402


RNG = np.random.default_rng(20260808)
FAILS: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name}  {detail}")
        FAILS.append(name)


def approx(a: float, b: float, tol: float = 1e-6) -> bool:
    return abs(a - b) <= tol


# ---------------------------------------------------------------------------

def test_exact_topk_matches_naive():
    print("\ntest_exact_topk_matches_naive")
    n, dim, nq, k = 200, 16, 10, 5
    vecs = RNG.random((n, dim), dtype=np.float32)
    qs = RNG.random((nq, dim), dtype=np.float32)
    ids = [f"v{i}" for i in range(n)]

    got = exact_topk(qs, ids, vecs, k, "l2")

    ok = True
    for qi in range(nq):
        d = ((vecs - qs[qi]) ** 2).sum(axis=1)      # honest squared euclidean
        want = [ids[j] for j in np.argsort(d, kind="stable")[:k]]
        if got[qi] != want:
            ok = False
            print(f"    q{qi}: got {got[qi]} want {want}")
    check("l2 top-k matches naive brute force", ok)

    got_ip = exact_topk(qs, ids, vecs, k, "ip")
    ok_ip = True
    for qi in range(nq):
        d = -(vecs @ qs[qi])
        want = [ids[j] for j in np.argsort(d, kind="stable")[:k]]
        if set(got_ip[qi]) != set(want):
            ok_ip = False
    check("ip top-k matches naive brute force", ok_ip)

    check("empty index yields empty truth",
          exact_topk(qs, [], np.zeros((0, dim), np.float32), k) == [[]] * nq)

    small = exact_topk(qs, ids[:3], vecs[:3], k)
    check("k larger than index returns all available",
          all(len(r) == 3 for r in small))


def test_recall_and_completeness():
    print("\ntest_recall_and_completeness")
    truth = ["a", "b", "c", "d", "e"]
    check("perfect recall", approx(recall_at_k(truth, truth, 5), 1.0))
    check("half recall", approx(recall_at_k(["a", "b", "x", "y", "z"], truth, 5), 0.4))
    check("zero recall", approx(recall_at_k(["v", "w", "x", "y", "z"], truth, 5), 0.0))
    check("order does not matter",
          approx(recall_at_k(list(reversed(truth)), truth, 5), 1.0))
    check("empty truth is NaN, not 1.0", np.isnan(recall_at_k(truth, [], 5)))

    check("completeness full", approx(set_completeness({"a", "b"}, {"a", "b"}), 1.0))
    check("completeness partial", approx(set_completeness({"a"}, {"a", "b"}), 0.5))
    check("completeness ignores extras",
          approx(set_completeness({"a", "b", "zz"}, {"a", "b"}), 1.0))
    check("empty intended is NaN", np.isnan(set_completeness({"a"}, set())))


def test_pairwise_agreement():
    print("\ntest_pairwise_agreement")
    obs = {"r0": [["a", "b", "c"]], "r1": [["a", "b", "c"]]}
    check("identical replicas agree fully", approx(pairwise_agreement(obs, 3), 1.0))

    obs = {"r0": [["a", "b", "c"]], "r1": [["a", "x", "y"]]}
    check("one-of-three overlap", approx(pairwise_agreement(obs, 3), 1 / 3))

    obs = {"r0": [["a", "b", "c"]], "r1": [["x", "y", "z"]]}
    check("disjoint replicas score zero", approx(pairwise_agreement(obs, 3), 0.0))

    check("single replica is NaN", np.isnan(pairwise_agreement({"r0": [["a"]]}, 3)))

    # A replica returning fewer results must not be scored as agreement.
    obs = {"r0": [["a", "b", "c"]], "r1": [["a"]]}
    check("short result list penalised (denominator is the longer list)",
          approx(pairwise_agreement(obs, 3), 1 / 3))


def test_leave_one_out_agreement():
    """The detection signal must single out the odd replica, not just report
    that the group as a whole disagrees."""
    print("\ntest_leave_one_out_agreement")

    # r0 and r1 agree with each other; r2 is the outlier.
    obs = {
        "r0": [["a", "b", "c"]],
        "r1": [["a", "b", "c"]],
        "r2": [["x", "y", "z"]],
    }
    loo = leave_one_out_agreement(obs, 3)
    check("outlier scores lowest",
          loo["r2"] < loo["r0"] and loo["r2"] < loo["r1"],
          f"got {loo}")
    check("the two agreeing replicas are not distinguished",
          approx(loo["r0"], loo["r1"]), f"got {loo}")
    check("agreeing pair scores 0.5 (1.0 with peer, 0.0 with outlier)",
          approx(loo["r0"], 0.5), f"got {loo['r0']}")
    check("outlier scores 0.0 against both", approx(loo["r2"], 0.0))

    # A healthy group: everyone identical, nobody singled out.
    same = {n: [["a", "b", "c"]] for n in ("r0", "r1", "r2")}
    loo_same = leave_one_out_agreement(same, 3)
    check("healthy group scores all replicas equally",
          approx(loo_same["r0"], 1.0) and approx(loo_same["r1"], 1.0)
          and approx(loo_same["r2"], 1.0))

    # Fewer than three replicas cannot single anyone out.
    loo2 = leave_one_out_agreement({"r0": [["a"]], "r1": [["b"]]}, 3)
    check("two replicas yield NaN (cannot attribute)",
          all(np.isnan(v) for v in loo2.values()))

    # Partial degradation should be ordered, not just flagged.
    obs3 = {
        "r0": [["a", "b", "c", "d"]],
        "r1": [["a", "b", "c", "d"]],
        "r2": [["a", "b", "x", "y"]],
    }
    loo3 = leave_one_out_agreement(obs3, 4)
    check("partially degraded replica still ranks lowest",
          loo3["r2"] < loo3["r0"], f"got {loo3}")


def test_corpus_gather():
    """The Corpus replaced a per-call dict-to-array rebuild. It has to return
    exactly what the old path did, or every measurement shifts silently."""
    print("\ntest_corpus_gather")

    n, dim, nq, k = 500, 24, 12, 8
    ids = [f"v{i}" for i in range(n)]
    vecs = RNG.random((n, dim), dtype=np.float32)
    vector_of = {i: vecs[j] for j, i in enumerate(ids)}
    qs = RNG.random((nq, dim), dtype=np.float32)
    c = Corpus.from_dict(vector_of)

    subset = set(ids[:137]) | set(ids[300:412])
    rows = c.rows_for(subset)
    check("rows_for returns one row per known id", len(rows) == len(subset))
    check("rows_for is ascending", bool(np.all(np.diff(rows) > 0)))
    check("rows_for maps back to the right ids",
          {c.ids[r] for r in rows} == subset)

    check("rows_for drops unknown ids",
          len(c.rows_for({"nope", "also-nope"})) == 0)
    check("rows_for on empty input is empty", len(c.rows_for(set())) == 0)

    # The gather path must agree with building the matrix the old way.
    sub_ids = [c.ids[r] for r in rows]
    old_mat = np.asarray([vector_of[i] for i in sub_ids], dtype=np.float32)
    check("gathered matrix equals the rebuilt one",
          bool(np.array_equal(c.mat[rows], old_mat)))

    want = exact_topk(qs, sub_ids, old_mat, k, "l2")
    got_rows = exact_topk_rows(qs, c.mat[rows], k, "l2")
    got = [[c.ids[rows[j]] for j in r] for r in got_rows]
    check("row-based top-k matches id-based top-k", got == want)

    # Growth must not disturb rows already handed out: a snapshot taken
    # before an append has to keep resolving correctly afterwards.
    grown = dict(vector_of)
    for j in range(50):
        grown[f"new{j}"] = RNG.random(dim, dtype=np.float32)
    c2 = Corpus.from_dict(grown)
    check("existing ids keep their vectors after growth",
          bool(np.array_equal(c2.mat[c2.rows_for({"v7"})][0], vector_of["v7"])))


def test_decomposition_separates_causes():
    """The core claim: index_recall isolates graph quality, completeness
    isolates data content, and e2e_recall reflects both."""
    print("\ntest_decomposition_separates_causes")

    n, dim, nq, k = 400, 32, 25, 10
    ids = [f"v{i}" for i in range(n)]
    vecs = RNG.random((n, dim), dtype=np.float32)
    vector_of = {i: vecs[j] for j, i in enumerate(ids)}
    qs = RNG.random((nq, dim), dtype=np.float32)
    intended = set(ids)

    truth_full = exact_topk(qs, ids, vecs, k, "l2")

    # (a) Healthy replica: has everything, search is exact.
    healthy = score_replica(qs, truth_full, set(ids), intended, Corpus.from_dict(vector_of), k)
    check("healthy: index_recall == 1", approx(healthy["index_recall"], 1.0))
    check("healthy: completeness == 1", approx(healthy["completeness"], 1.0))
    check("healthy: e2e_recall == 1", approx(healthy["e2e_recall"], 1.0))

    # (b) Data loss: holds 70% of the set, but searches its own data perfectly.
    #     index_recall must stay ~1 (nothing wrong with the graph);
    #     completeness must drop to 0.7; e2e must drop.
    kept = ids[:280]
    kept_mat = np.asarray([vector_of[i] for i in kept], dtype=np.float32)
    obs_lossy = exact_topk(qs, kept, kept_mat, k, "l2")
    lossy = score_replica(qs, obs_lossy, set(kept), intended, Corpus.from_dict(vector_of), k)
    check("data-loss: index_recall stays 1 (graph is fine)",
          approx(lossy["index_recall"], 1.0),
          f"got {lossy['index_recall']:.4f}")
    check("data-loss: completeness drops to 0.7",
          approx(lossy["completeness"], 0.7))
    check("data-loss: e2e_recall drops below 1",
          lossy["e2e_recall"] < 0.98, f"got {lossy['e2e_recall']:.4f}")

    # (c) Degraded graph: holds everything, but search returns poor results.
    #     completeness must stay 1; index_recall must drop.
    obs_bad = [t[:3] + [f"v{int(RNG.integers(0, n))}" for _ in range(k - 3)]
               for t in truth_full]
    degraded = score_replica(qs, obs_bad, set(ids), intended, Corpus.from_dict(vector_of), k)
    check("degraded-graph: completeness stays 1 (data is fine)",
          approx(degraded["completeness"], 1.0))
    check("degraded-graph: index_recall drops",
          degraded["index_recall"] < 0.6, f"got {degraded['index_recall']:.4f}")

    # The discriminating assertion: the two failures are distinguishable.
    check("the two failures are told apart by opposite metrics",
          lossy["index_recall"] > degraded["index_recall"]
          and degraded["completeness"] > lossy["completeness"],
          f"lossy={lossy}, degraded={degraded}")

    # (d) e2e is bounded above by what the replica can possibly return.
    check("e2e_recall <= completeness for a data-lossy replica",
          lossy["e2e_recall"] <= lossy["completeness"] + 1e-9)


def test_agreement_tracks_divergence():
    """Sanity check on the Layer-3 premise: when replicas genuinely diverge,
    ground-truth-free agreement should fall. This does not prove agreement is
    a good detector -- that is what the live experiment is for -- only that
    the statistic responds in the right direction."""
    print("\ntest_agreement_tracks_divergence")

    n, dim, nq, k = 300, 16, 20, 10
    ids = [f"v{i}" for i in range(n)]
    vecs = RNG.random((n, dim), dtype=np.float32)
    vector_of = {i: vecs[j] for j, i in enumerate(ids)}
    qs = RNG.random((nq, dim), dtype=np.float32)

    full = exact_topk(qs, ids, vecs, k, "l2")

    prev = 1.01
    monotone = True
    for frac in (1.0, 0.9, 0.7, 0.5):
        kept = ids[:int(n * frac)]
        mat = np.asarray([vector_of[i] for i in kept], dtype=np.float32)
        obs = exact_topk(qs, kept, mat, k, "l2")
        agree = pairwise_agreement({"r0": full, "r1": obs}, k)
        print(f"    replica holds {frac:.0%} of data -> agreement {agree:.4f}")
        if agree > prev + 1e-9:
            monotone = False
        prev = agree
    check("agreement falls as one replica loses more data", monotone)


# ---------------------------------------------------------------------------
# SIFT loader. These run without touching the network or the filesystem: the
# fvecs parser accepts a bytes buffer, so the tests synthesise their own.
# ---------------------------------------------------------------------------

def _fvecs_bytes(vecs: np.ndarray, dim_header: int | None = None) -> bytes:
    """Encode an (n, dim) float32 array as fvecs. dim_header overrides the
    declared dimension, so a test can forge a corrupt record."""
    n, dim = vecs.shape
    hdr = np.full((n, 1), dim if dim_header is None else dim_header,
                  dtype=np.int32)
    return np.hstack([hdr.view(np.float32), vecs.astype(np.float32)]).tobytes()


def test_fvecs_roundtrip():
    print("\ntest_fvecs_roundtrip")
    dim = 8
    vecs = RNG.integers(0, 256, size=(6, dim)).astype(np.float32)
    buf = _fvecs_bytes(vecs)

    check("record stride is 4 + 4*dim", len(buf) == 6 * (4 + 4 * dim),
          f"{len(buf)}")

    got = sift.read_fvecs(buf, dim=dim)
    check("shape survives the round trip", got.shape == (6, dim), str(got.shape))
    check("dtype is float32", got.dtype == np.float32, str(got.dtype))
    check("values are bit-identical", bool((got == vecs).all()))

    # limit must read a prefix, not a sample.
    head = sift.read_fvecs(buf, limit=2, dim=dim)
    check("limit returns the first n records",
          head.shape == (2, dim) and bool((head == vecs[:2]).all()))


def test_fvecs_rejects_corruption():
    print("\ntest_fvecs_rejects_corruption")
    dim = 8
    vecs = RNG.integers(0, 256, size=(4, dim)).astype(np.float32)
    rec = 4 + 4 * dim

    def raises(fn) -> bool:
        try:
            fn()
        except ValueError:
            return True
        except Exception:
            return False
        return False

    # A truncated download is the failure that would silently shrink the
    # corpus and quietly invalidate a sweep, so it must be loud.
    check("truncated mid-record raises",
          raises(lambda: sift.read_fvecs(_fvecs_bytes(vecs)[:rec * 2 + 5],
                                         dim=dim)))
    check("wrong dimension header raises",
          raises(lambda: sift.read_fvecs(_fvecs_bytes(vecs, dim_header=64),
                                         dim=dim)))
    check("empty buffer raises",
          raises(lambda: sift.read_fvecs(b"", dim=dim)))
    check("limit beyond end of data raises",
          raises(lambda: sift.read_fvecs(_fvecs_bytes(vecs), limit=99,
                                         dim=dim)))


def test_sift_scaling_preserves_ranking():
    print("\ntest_sift_scaling_preserves_ranking")
    # SIFT-shaped data: 128-d, integer components 0..255. At that magnitude
    # ||v||^2 approaches float32's exact-integer ceiling, and exact_topk_rows
    # uses the expanded form ||v||^2 - 2*q.v, whose cancellation is worst
    # exactly there. sift.SCALE divides by 128 -- a power of two, so a bare
    # exponent decrement with no mantissa rounding. This asserts the property
    # that justifies doing it at all: the ranking must not move.
    n, dim, nq, k = 400, 128, 25, 10
    raw = RNG.integers(0, 256, size=(n, dim)).astype(np.float32)
    q_raw = RNG.integers(0, 256, size=(nq, dim)).astype(np.float32)

    hi = exact_topk_rows(q_raw, raw, k, "l2")
    lo = exact_topk_rows(q_raw * np.float32(sift.SCALE),
                         raw * np.float32(sift.SCALE), k, "l2")
    check("top-k ranking identical after scaling by 1/128",
          bool((hi == lo).all()),
          f"{int((hi != lo).sum())} of {hi.size} positions differ")

    # The scaling itself must be exact, not merely order-preserving.
    check("scaling is lossless (x/128*128 == x)",
          bool(((raw * np.float32(sift.SCALE)) * np.float32(128.0) == raw).all()))


# ---------------------------------------------------------------------------
# Graph forensics. The tool reads a C++ struct straight off disk, so the only
# thing between it and a confidently wrong conclusion is a test that builds an
# index with a KNOWN graph and checks the metrics recover it.
# ---------------------------------------------------------------------------

def _build_index(vecs: np.ndarray, adjacency: list[list[int]],
                 entry: int = 0, element_count: int | None = None) -> bytes:
    """Synthesise an index.ndb with a chosen layer-0 graph."""
    n = len(vecs)
    nodes = np.zeros(n, dtype=gf.NODE_DTYPE)
    nodes["neighbors"][:] = gf.NO_NEIGHBOR      # as the Node constructor does
    nodes["id"] = np.arange(n)
    nodes["vector"][:, :vecs.shape[1]] = vecs
    for i, nbr in enumerate(adjacency):
        nodes["neighbor_counts"][i, 0] = len(nbr)
        if nbr:
            nodes["neighbors"][i, 0, :len(nbr)] = nbr

    hdr = np.zeros(1, dtype=gf.HEADER_DTYPE)
    hdr["magic"] = gf.NANODB_MAGIC
    hdr["element_count"] = n if element_count is None else element_count
    hdr["entry_point_id"] = entry
    hdr["max_layer"] = 0
    return hdr.tobytes() + nodes.tobytes()


def test_forensics_layout():
    print("\ntest_forensics_layout")
    # These come from offsetof/sizeof on the compiled struct. If the C++ ever
    # changes, this test is the tripwire -- every forensic number is otherwise
    # silently reinterpreting the wrong bytes.
    check("Node is 1056 bytes", gf.NODE_DTYPE.itemsize == 1056,
          str(gf.NODE_DTYPE.itemsize))
    check("FileHeader is 64 bytes", gf.HEADER_DTYPE.itemsize == 64)
    off = {k: gf.NODE_DTYPE.fields[k][1] for k in gf.NODE_DTYPE.names}
    check("vector @ 12", off["vector"] == 12, str(off["vector"]))
    check("neighbors @ 524", off["neighbors"] == 524, str(off["neighbors"]))
    check("neighbor_counts @ 1036", off["neighbor_counts"] == 1036,
          str(off["neighbor_counts"]))

    vecs = RNG.random((6, 128), dtype=np.float32)
    buf = _build_index(vecs, [[1], [2], [3], [4], [5], [0]])
    hdr, nodes, cap = gf.load_index(buf)
    check("round-trips node count", len(nodes) == 6, str(len(nodes)))
    check("round-trips vectors", bool(np.allclose(nodes["vector"], vecs)))
    check("bad magic raises",
          _raises(lambda: gf.load_index(b"\x00" * 4096), ValueError))


def _raises(fn, exc) -> bool:
    try:
        fn()
    except exc:
        return True
    except Exception:
        return False
    return False


def test_forensics_finds_known_damage():
    print("\ntest_forensics_finds_known_damage")
    vecs = RNG.random((8, 128), dtype=np.float32)
    # A ring, so every node has exactly one in-edge and one out-edge...
    adj = [[(i + 1) % 8] for i in range(8)]
    # ...then break it in three specific, separately-detectable ways.
    adj[3] = []           # node 3: out-degree 0 -- and node 4 loses its only
    #                       in-edge as a side effect
    adj[2] = [99]         # node 2 -> nonexistent id: a dangling edge, and
    #                       node 3 loses its only in-edge
    adj[5] = [5, 6]       # node 5: a self-loop (6 keeps its in-edge)
    buf = _build_index(vecs, adj, entry=0)
    r = gf.analyse(buf)

    check("counts the written nodes", r["nodes_examined"] == 8,
          str(r["nodes_examined"]))
    check("finds the out-degree-0 node", r["out_degree_0"] == 1,
          str(r["out_degree_0"]))
    check("finds the dangling edge", r["dangling_edges"] == 1,
          str(r["dangling_edges"]))
    check("finds the self loop", r["self_loops"] == 1, str(r["self_loops"]))
    # Two nodes end up with nothing pointing at them: node 3 (node 2 was
    # redirected to id 99) and node 4 (node 3's list was emptied). Severing an
    # edge orphans the node at the far end, which is exactly the accounting
    # the real analysis depends on.
    check("finds the nodes nothing points at", r["in_degree_0"] == 2,
          f"{r['in_degree_0']}")
    # The ring is severed at 2, so BFS from 0 reaches 0,1,2 and stops.
    check("BFS stops at the break", r["reachable_from_entry"] == 3,
          str(r["reachable_from_entry"]))

    # A node written but never counted is the population the mechanism
    # hypothesis predicts; the extent scan must see past element_count.
    buf2 = _build_index(vecs, [[1]] * 8, entry=0, element_count=5)
    r2 = gf.analyse(buf2)
    check("scans past element_count", r2["nodes_examined"] == 8,
          str(r2["nodes_examined"]))
    check("reports the uncounted nodes", r2["uncounted_nodes"] == 3,
          str(r2["uncounted_nodes"]))


def test_link_quality_ranks_graphs():
    print("\ntest_link_quality_ranks_graphs")
    n, dim, m = 60, 16, 4
    vecs = RNG.random((n, dim), dtype=np.float32)

    # Exact m nearest neighbours of every node, by brute force.
    d = ((vecs[:, None, :] - vecs[None, :, :]) ** 2).sum(-1)
    np.fill_diagonal(d, np.inf)
    truth = np.argsort(d, axis=1)[:, :m]
    worst = np.argsort(d, axis=1)[:, -m:]

    perfect = gf.link_quality(gf.load_index(_build_index(
        vecs, [list(row) for row in truth]))[1], sample=n, m=m)
    awful = gf.link_quality(gf.load_index(_build_index(
        vecs, [list(row) for row in worst]))[1], sample=n, m=m)

    check("perfect neighbours score 1.0",
          approx(perfect["link_quality"], 1.0, 1e-9),
          f"{perfect['link_quality']:.4f}")
    check("farthest neighbours score 0.0",
          approx(awful["link_quality"], 0.0, 1e-9),
          f"{awful['link_quality']:.4f}")

    # The metric has to be monotone in between, or a small real degradation
    # would not register as a smaller number.
    prev, monotone = 1.0 + 1e-9, True
    for keep in (4, 3, 2, 1, 0):
        adj = [list(truth[i][:keep]) + list(worst[i][:m - keep])
               for i in range(n)]
        q = gf.link_quality(gf.load_index(_build_index(vecs, adj))[1],
                            sample=n, m=m)["link_quality"]
        print(f"    {keep}/{m} true neighbours kept -> link quality {q:.4f}")
        if q > prev + 1e-9:
            monotone = False
        prev = q
    check("degrades monotonically as links get worse", monotone)


if __name__ == "__main__":
    test_exact_topk_matches_naive()
    test_recall_and_completeness()
    test_pairwise_agreement()
    test_leave_one_out_agreement()
    test_corpus_gather()
    test_decomposition_separates_causes()
    test_agreement_tracks_divergence()
    test_fvecs_roundtrip()
    test_fvecs_rejects_corruption()
    test_sift_scaling_preserves_ranking()
    test_forensics_layout()
    test_forensics_finds_known_damage()
    test_link_quality_ranks_graphs()

    print()
    if FAILS:
        print(f"FAILED ({len(FAILS)}): " + ", ".join(FAILS))
        sys.exit(1)
    print("all metric tests passed")
