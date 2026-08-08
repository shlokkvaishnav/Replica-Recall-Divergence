"""
Measurement core for the replica-recall experiment.

Pure functions only -- no network, no cluster, no global state. Everything
here is exercised by test_metrics.py without a running cluster, because the
whole experiment rests on these being right.

--------------------------------------------------------------------------
Why this decomposition
--------------------------------------------------------------------------
When a replica of an approximate index returns a bad answer, there are at
least four distinct causes, and the returned answer looks identical in all
four cases:

  (a) healthy replica, ordinary ANN nondeterminism
  (b) replica is missing data (replication loss / lag)
  (c) replica's graph degraded from churn (tombstones, stale entry point)
  (d) replica is stale w.r.t. recent writes

A single "recall dropped" number cannot separate these. So we measure the
replica against two *different* ground truths and take the difference:

  index_recall   = recall of the replica's search against exact top-k over
                   THE REPLICA'S OWN live set.
                   -> data content is held constant, so this isolates
                      graph/ANN quality. Cause (c).

  completeness   = |replica live set  n  intended set| / |intended set|
                   -> ignores search entirely, so this isolates data
                      content. Causes (b) and (d).

  e2e_recall     = recall of the replica's search against exact top-k over
                   the INTENDED set.
                   -> what a client actually experiences if routed here.
                      Bounded above by both of the above.

  agreement      = mean pairwise overlap between replicas of the same shard,
                   computed with NO ground truth at all.
                   -> this is the only one of the four that is observable in
                      production. The point of the experiment is to find out
                      whether it tracks the other three. If it does, it is a
                      usable detector for a failure mode that is currently
                      silent.

--------------------------------------------------------------------------
Defining the "intended set"
--------------------------------------------------------------------------
A replica of shard 0 legitimately does not hold shard 1's vectors, so the
intended set has to be per-shard. Rather than reimplement the coordinator's
consistent-hash routing in Python (which would silently drift from the C++
whenever the ring changes), we derive it empirically:

    intended(s) = ( union of live ids across all replicas of shard s )
                  n ( ids the writer has confirmed and not deleted )

This is a lower bound on what every replica of s should hold, and measures
divergence *within* a replica group -- which is the phenomenon under study.

The case it deliberately does not catch is a confirmed id that ALL replicas
of a shard lost. That is total data loss rather than divergence, it is a
different failure, and chaos_harness.py's existing invariant #1 already
covers it via the coordinator. Keeping the two harnesses responsible for
different things is intentional.
"""

from __future__ import annotations

import numpy as np


# ---------------------------------------------------------------------------
# Exact search (ground truth)
# ---------------------------------------------------------------------------

def exact_topk(queries: np.ndarray,
               ids: list[str],
               vectors: np.ndarray,
               k: int,
               metric: str = "l2") -> list[list[str]]:
    """Exact top-k by brute force. Returns, per query, a list of external ids
    ordered nearest-first.

    queries : (nq, dim) float32
    ids     : length-n list of external ids, aligned with `vectors` rows
    vectors : (n, dim) float32
    metric  : 'l2' (squared euclidean) or 'ip' (negative inner product)

    'l2' matches nanodb's DistanceMetric::L2, which is squared euclidean --
    it omits the sqrt because that preserves ranking (see config/types.hpp).
    """
    if len(ids) == 0:
        return [[] for _ in range(len(queries))]

    k_eff = min(k, len(ids))

    if metric == "l2":
        # ||q - x||^2 = ||q||^2 - 2 q.x + ||x||^2
        # ||q||^2 is constant within a query row, so it does not affect the
        # ranking and is dropped.
        d = (vectors * vectors).sum(axis=1)[None, :] - 2.0 * (queries @ vectors.T)
    elif metric == "ip":
        d = -(queries @ vectors.T)
    else:
        raise ValueError(f"unknown metric: {metric!r}")

    # argpartition for the k smallest, then sort just that slice.
    part = np.argpartition(d, k_eff - 1, axis=1)[:, :k_eff]
    rows = np.arange(len(queries))[:, None]
    order = np.argsort(d[rows, part], axis=1)
    top = part[rows, order]

    return [[ids[j] for j in row] for row in top]


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def recall_at_k(observed: list[str], truth: list[str], k: int) -> float:
    """Fraction of the exact top-k that the approximate search actually found.

    Set overlap, not rank correlation -- this is the standard ANN recall@k
    convention (and what ann-benchmarks reports), so numbers stay comparable
    to published results.

    Undefined when the truth set is empty (nothing to find); returns NaN so
    it drops out of downstream means rather than silently scoring 1.0.
    """
    if not truth:
        return float("nan")
    t = set(truth[:k])
    o = set(observed[:k])
    return len(o & t) / len(t)


def set_completeness(local: set[str], intended: set[str]) -> float:
    """Fraction of the intended set that this replica actually holds.

    NaN when nothing is intended yet, for the same reason as recall_at_k.
    """
    if not intended:
        return float("nan")
    return len(local & intended) / len(intended)


def pairwise_agreement(obs_by_replica: dict[str, list[list[str]]], k: int) -> float:
    """Mean pairwise top-k overlap across replicas of one shard.

    obs_by_replica : replica name -> per-query list of returned id lists

    This is the ground-truth-free observable. In production you can compute
    exactly this and nothing else, which is why the experiment records it
    alongside the true metrics: the question is whether it tracks them.

    NaN with fewer than two replicas (no pair to compare).
    """
    names = sorted(obs_by_replica)
    if len(names) < 2:
        return float("nan")

    scores: list[float] = []
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            a_all, b_all = obs_by_replica[names[i]], obs_by_replica[names[j]]
            for a, b in zip(a_all, b_all):
                sa, sb = set(a[:k]), set(b[:k])
                if not sa and not sb:
                    continue          # both empty: no information, not agreement
                denom = max(len(sa), len(sb))
                scores.append(len(sa & sb) / denom)

    return float(np.mean(scores)) if scores else float("nan")


# ---------------------------------------------------------------------------
# Per-replica sample
# ---------------------------------------------------------------------------

def score_replica(queries: np.ndarray,
                  observed: list[list[str]],
                  local_ids: set[str],
                  intended_ids: set[str],
                  vector_of: dict[str, np.ndarray],
                  k: int,
                  metric: str = "l2") -> dict[str, float]:
    """Compute the three ground-truth metrics for one replica at one instant.

    observed     : per-query id lists returned by THIS replica's Search
    local_ids    : what this replica reports holding (ListLocalIds)
    intended_ids : what every replica of this shard should hold
    vector_of    : external id -> vector, retained by the writer

    Ids the harness has no vector for are excluded from ground truth: they
    cannot be scored, and guessing would corrupt the measurement.
    """
    local_known = [i for i in sorted(local_ids) if i in vector_of]
    intended_known = [i for i in sorted(intended_ids) if i in vector_of]

    def _mean_recall(truth_ids: list[str]) -> float:
        if not truth_ids:
            return float("nan")
        mat = np.asarray([vector_of[i] for i in truth_ids], dtype=np.float32)
        truth = exact_topk(queries, truth_ids, mat, k, metric)
        per_query = [recall_at_k(o, t, k) for o, t in zip(observed, truth)]
        per_query = [r for r in per_query if not np.isnan(r)]
        return float(np.mean(per_query)) if per_query else float("nan")

    return {
        "index_recall": _mean_recall(local_known),
        "e2e_recall": _mean_recall(intended_known),
        "completeness": set_completeness(local_ids, intended_ids),
        "n_local": float(len(local_ids)),
        "n_intended": float(len(intended_ids)),
    }
