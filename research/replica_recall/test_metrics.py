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


if __name__ == "__main__":
    test_exact_topk_matches_naive()
    test_recall_and_completeness()
    test_pairwise_agreement()
    test_leave_one_out_agreement()
    test_corpus_gather()
    test_decomposition_separates_causes()
    test_agreement_tracks_divergence()

    print()
    if FAILS:
        print(f"FAILED ({len(FAILS)}): " + ", ".join(FAILS))
        sys.exit(1)
    print("all metric tests passed")
