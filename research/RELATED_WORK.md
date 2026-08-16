# Related work: what is actually unclaimed

This document positions the finding in `research/replica_recall/` against
published work. It exists to keep the project honest: the claims here are
narrower than the ones that first suggested themselves, and the last section
lists the things we must **not** say because someone else already owns them.

**Status of the citations.** Gathered by literature search in August 2026, then
independently verified against source — every arXiv ID resolves and matches its
claimed content, but one citation (ClickHouse #104674, previously the lead
production-evidence item) turned out to be a self-retracted bug report and has
been removed rather than softened; see §6. Two smaller corrections — an
overstated "ground-truth-free" claim (§5, entry 11) and a misattributed author
on the STTT citation (§1) — are marked inline where they occur. Still worth a
second read before external submission, particularly the 2026 entries, which
are recent enough that their own claims may still move.

---

## The claim

An exact key-value store that loses a row returns *not found*. That is an
observable event, and a Jepsen-family checker can flag it.

An approximate nearest-neighbour index that loses vectors still returns *k*
plausible neighbours. Nothing about the response says anything is wrong.
**Approximation converts data loss into silence.**

Measured on nano-db (2 shards × 3 replicas, node-kill chaos, five seeds,
`--dist lowdim`):

| | baseline | chaos |
|---|---|---|
| within-shard recall spread | 0.0001 ± 0.0002 | 0.0603 ± 0.0269 |
| `index_recall` (graph quality) | 0.9916 ± 0.0030 | 0.9718 ± 0.0097 |
| `completeness` (data content) | 1.0000 ± 0.0000 | 0.9615 ± 0.0143 |

All at the exact Mann-Whitney floor for 5v5, p = 0.0079. After chaos stops and
the cluster fully recovers, `miss@stop == miss@end` in 5/5 runs — **0% of the
lost vectors come back**. Independently built replicas agree to 1e-4, so ANN
nondeterminism contributes essentially nothing to the divergence.

---

## Per-area findings

### 1. Correctness criteria for replicated approximate indexes — **GAP**

The strongest part of the claim. Nothing found formalises what "correct" means
for a replicated ANN index.

The closest prior art, and the one this must be built on top of, is
**Wang et al., *Towards Reliable Vector Database Management Systems: A Software
Testing Roadmap for 2030*** (arXiv:2502.20812, Feb 2025). It names the *oracle
problem* for vector databases explicitly — ANN's nondeterministic ε-error bounds
render boolean assertions ineffective, and differential testing cannot separate
a bug from an expected approximation. It categorises 79+ bugs across Milvus,
Qdrant, Chroma and Weaviate.

It is also a gift, because **it never discusses replication, node failure, fault
injection, or distributed consistency.** It states the problem in the single-node
setting and leaves the replicated setting open. That is exactly the seam this
work sits in.

**Formal lineage:** quasi-linearizability (Afek, Korland & Yanovsky, OPODIS 2010;
verification in Adhikari et al., STTT 2015/2016 —
correcting an earlier misattribution to "Zhang et al."; Zhang is a coauthor,
not first author) and quantitative quiescent consistency
(Jagadeesan & Riely, ICALP 2014) are the right ancestors for "approximately
correct read" — but they relax *ordering* by a bounded amount, not *set
membership under an approximate retrieval function*. Cite as lineage; do not
claim to have invented relaxed correctness.

**Cite and distinguish:** *Coordination-Free Lane Partitioning for Convergent ANN
Search* (arXiv:2511.04221) sounds like a collision but concerns disjoint
candidate partitioning within a single query, not replica convergence.

### 2. Jepsen-family testing of vector databases — **GAP**

`jepsen.io/analyses` lists **no vector database**. Not Milvus, Qdrant, Weaviate,
Pinecone, or Vespa. The nearest targets are Kingsbury's two Elasticsearch
analyses (1.1.0, 2014; 1.5.0, 2015), and both check **document presence and
acknowledged-write loss** — precisely the exact-store model this work contrasts
against.

**Elle** (Kingsbury & Alvaro, PVLDB 2020) infers transactional anomalies from
observed traces, and its notion of a read is exact value equality. No consistency
checker found has any notion of an *approximately correct* read.

Caveat on evidence: this is a strong absence, not a proof. It is the kind of
claim that should be stated as "we could not find" rather than "there is none".

**But chaos testing of vector stores does exist.** Milvus runs an in-house Chaos
Mesh suite — pod-kill, pod-failure, memory stress, network partition — and files
bugs from it. Their assertions are availability and entity count, never recall.
That is the interesting detail, and it is a much better sentence than "nobody
tests this": *the vendor's own chaos suite would not catch what we measured.*

### 3. Streaming / fresh ANN benchmarks — **COVERS churn, GAP on failure**

FreshDiskANN (arXiv:2105.09613), SPFresh (SOSP '23), the NeurIPS'23 Big-ANN
streaming track (arXiv:2409.17424), and the 2025–26 successors (LSM-VEC, DGAI,
VIBE) all study recall decay under **insert/delete churn on a single index**.
The Big-ANN streaming track is a runbook of inserts, deletes and searches
executed within an hour on one machine, scored by recall@10.

**None injects node failure. None has a replication dimension. None reports
cross-replica variance.** The distinction holds, but it must be stated precisely:
*these measure recall stability under update churn on a single index* — not
"these don't measure recall stability", which would be false.

### 4. Anti-entropy for approximate contents — **COVERS the data, GAP on the index**

Every production anti-entropy mechanism found operates on **exact object
identity**:

- **Weaviate** async replication (GA in v1.29, Feb 2025) uses a real Merkle/hash
  tree over object digests, configurable via `ASYNC_REPLICATION_HASHTREE_HEIGHT`.
- **Vespa** reconciles replicas by exchanging sets of timestamped documents with
  checksummed metadata.
- **Dynamo** (DeCandia et al., SOSP 2007) and Cassandra repair: Merkle trees over
  exact key-value hashes.

The structural point: **two correct HNSW graphs built over identical data differ
bit-for-bit**, so none of these mechanisms can be pointed at the index. Elastic's
own writeup confirms that every *shard* builds its own independent HNSW graph
per segment; the extension to every *replica* is a standard-architecture
inference (each replica is itself sharded the same way), not something that
specific page states about replicas directly. Hash-based comparison is simply
not available for the thing that degraded.

This forces an honest weakening of our healing result, handled in §Framing risks.

### 5. Ground-truth-free ANN quality estimation — **PARTIAL**

The technique class is old and the ANN-specific instances already exist:

- ***ANN Search: Recall What Matters*** (Dimitropoulos et al., arXiv:2606.04522)
  proposes `1/Ratio@k`, "judge-free, hyperparameter-free... computable from the
  same inputs ANN benchmarks already provide." **Correction:** an earlier draft
  called this "ground-truth-free"; it is not. The metric is defined over
  differences between the retrieved and *true* nearest-neighbour distances, so
  it still needs exact ground truth to compute — "judge-free" (no LLM/human
  judge) is not the same property. It is the closest hit for *low-overhead*
  ANN quality estimation, not for the ground-truth-free property this project
  actually needs.
- ***Semantic Recall for Vector Search*** (SIGIR 2026) introduces *Tolerant
  Recall* as a proxy for when relevant objects cannot be identified.
- ***Towards Robustness: A Critique of Current Vector Database Assessments***
  (arXiv:2507.00379) proposes Robustness-δ@K and argues average recall hides
  variability — but **across queries**, not across replicas.
- Ensemble / jackknife disagreement as a reference-free quality signal is
  standard practice across many fields and decades.

So `leave_one_out_agreement` is not a new estimator, and claiming it as one
invites an easy rejection. What appears unclaimed is the **attribution
argument**: these are replicas of the same shard, which are *supposed to be
identical*, so disagreement between them is damage rather than model diversity.
The 1e-4 independently-built-replica control is what makes that attribution
sound. Claim the argument and the control, not the technique.

### 6. Production evidence — **moderate**

Ranked by how directly each supports "approximation converts data loss into
silence". **Correction:** an earlier version of this section led with
ClickHouse #104674 as the strongest evidence and called its resolution "the
argument." A verification pass reading the full comment thread found the
reporter **retracted their own report** — on retest, the ANN/brute-force
disagreement reproduced on a clean server with no crash involved; it was a
bf16-quantization precision artifact, not a durability bug, and there was no
maintainer dismissal to cite. It is removed below. This is exactly the failure
mode this section warns about elsewhere (§8, framing risks) — a citation that
sounds too convenient checked out to be wrong, which is why every citation here
has now been read past its headline.

1. **Milvus #37703** — after chaos and *nominal* recovery, search failed with
   `segment lacks`, 69% success rate (`Op.search succ rate 0.6933...` in the
   issue's own log). **This one errored loudly**, and is a genuine
   counterexample to universal silence — cite it honestly as that, not as an
   instance of the silent-failure thesis.
2. **Qdrant #4626** — confirmed: upserts committed by majority during a node
   restart never sync to the node once it comes back.
3. **Qdrant #4627** — confirmed: deletes during a restart leave the restarted
   node's points with empty, unsynced payloads.
4. **Milvus #30254** — confirmed: `NoSuchKey` errors on segment files, traced to
   the garbage collector deleting segments still in use.
5. **Pinecone's observability blog** states the thesis in prose, close to but
   not exactly the wording an earlier draft quoted verbatim — the actual text is
   "a stale, undersized, or under-resourced index doesn't go down. It returns
   the wrong results. The problem is that without continuous visibility into
   index health... there's no signal [something is wrong]." Paraphrase, don't
   quote, if this is used. A vendor stating the thesis in prose is good
   motivation and a reminder that the *observation* is not novel — only the
   measurement is.

**What was not found:** any public postmortem or engineering writeup that
*quantified* a recall divergence between replicas of the same shard, still. The
motivation gap is now sharper without ClickHouse #104674 to lean on — Milvus
#37703 and the Qdrant pair are real but none of them isolate a recall number
the way this project's `index_recall`/`completeness` decomposition does. A
reviewer may reasonably ask who was harmed, and the honest answer is "no public
report of exactly this, only adjacent bugs" — which is closer to the actual
strength of the motivation than the earlier draft implied.

---

## Novelty verdict

Unclaimed is the **conjunction**, not any single element:

1. Recall measured **per replica within a shard**, under node-kill chaos
2. The **three-way decomposition** — `index_recall` (graph quality, data held
   constant) vs `completeness` (data content, no search) vs `e2e_recall` (client
   experience). Vendor blogs gesture at "index quality vs retrieval quality";
   nobody isolates completeness as a replication-damage diagnostic.
3. **`miss@stop == miss@end` after full recovery.** The sharpest claim, because
   it is falsifiable, surprising, and unmeasured.

The framing "approximation converts data loss into silence, so no Jepsen-family
checker can flag it" is defensible: no checker found models an approximately
correct read, and no Jepsen analysis has targeted a vector store.

**Already well-trodden — cite, do not claim:** the ANN oracle problem
(arXiv:2502.20812 states it in the abstract); average recall hiding variance
(arXiv:2507.00379); low-overhead ANN quality estimation (arXiv:2606.04522,
SIGIR'26); jackknife/ensemble disagreement; recall decay under churn
(FreshDiskANN, SPFresh, Big-ANN'23); Merkle-tree anti-entropy (Dynamo); and the
general observation that a degraded index returns worse answers instead of
paging you — Pinecone says this in a marketing post.

---

## Must-cite

| # | Reference | URL | Why |
|---|---|---|---|
| 1 | Wang et al., *Towards Reliable VDBMS: A Software Testing Roadmap for 2030*, arXiv:2502.20812, 2025 | https://arxiv.org/abs/2502.20812 | States the ANN oracle problem; omits replication → the seam |
| 2 | Wang, Zhang, Lu, Chen, Tan, *Towards Robustness: A Critique of Current Vector DB Assessments*, arXiv:2507.00379 | https://arxiv.org/abs/2507.00379 | Recall-variance critique, query-level not replica-level |
| 3 | Simhadri et al., *Results of the Big ANN: NeurIPS'23 Competition*, arXiv:2409.17424 | https://arxiv.org/abs/2409.17424 | Streaming track = churn on one node, not failure |
| 4 | Singh, Subramanya, Krishnaswamy, Simhadri, *FreshDiskANN*, arXiv:2105.09613 | https://arxiv.org/abs/2105.09613 | Canonical streaming ANN baseline |
| 5 | Xu et al., *SPFresh*, SOSP '23 | https://dl.acm.org/doi/10.1145/3600006.3613166 | In-place update, single node |
| 6 | Kingsbury, *Jepsen: Elasticsearch 1.5.0*, 2015 | https://aphyr.com/posts/323-jepsen-elasticsearch-1-5-0 | Presence-based loss detection = the contrast case |
| 7 | Kingsbury & Alvaro, *Elle*, PVLDB 2020 | https://arxiv.org/abs/2003.10554 | Checker state of the art; exact reads only |
| 8 | DeCandia et al., *Dynamo*, SOSP 2007 | https://doi.org/10.1145/1294261.1294281 | Merkle anti-entropy assumes exact contents |
| 9 | Weaviate async replication (hash-tree digests), v1.29 | https://docs.weaviate.io/deploy/configuration/async-rep | Production Merkle anti-entropy over objects, not the index |
| 10 | Vespa consistency model | https://docs.vespa.ai/en/content/consistency.html | "Metadata is checksummed"; silent on the ANN graph |
| 11 | Dimitropoulos et al., *ANN Search: Recall What Matters* (1/Ratio@k), arXiv:2606.04522 | https://arxiv.org/abs/2606.04522 | Low-overhead ANN quality metric — needs true-NN distances, so not actually ground-truth-free; do not call it that |
| 12 | *Semantic Recall for Vector Search*, SIGIR 2026 | https://doi.org/10.1145/3805712.3809894 | Proxy recall without identifiable ground truth |
| 13 | Milvus #37703 — chaos recovery leaves search failing (`segment lacks`, 69% success) | https://github.com/milvus-io/milvus/issues/37703 | Best production evidence — genuinely LOUD failure, cite as the honest counterexample, not as silent-failure support |
| 14 | Qdrant #4626 / #4627 — missed upserts and deletes after node restart | https://github.com/qdrant/qdrant/issues/4626 | Replica divergence in a shipping vector DB |
| 15 | Milvus OSS QA / Chaos Mesh testing | https://milvus.io/blog/deep-dive-6-oss-qa.md | Vendor chaos suite asserts liveness and counts, not recall |
| 16 | Afek/Korland/Yanovsky quasi-linearizability; Adhikari et al., STTT 2015 | https://link.springer.com/article/10.1007/s10009-015-0373-2 | Formal lineage for relaxed correctness |

**Removed:** ClickHouse #104674 was in this slot in an earlier draft as "best
production evidence." Verification found the reporter retracted it — the
disagreement was a bf16-precision artifact reproducing with no crash, not a
durability bug. See §6 above.

**Graph-degradation mechanism** (for `graph_forensics.py`'s hypothesis —
degradation without topological damage — rather than the replication claim
above):

| # | Reference | URL | Why |
|---|---|---|---|
| 17 | Elliott & Clark, *The Impacts of Data, Ordering, and Intrinsic Dimensionality on Recall in HNSW*, arXiv:2405.17813, 2024 | https://arxiv.org/abs/2405.17813 | HNSW is not insertion-order invariant — ordering shifts recall up to 12pp. Supports the mechanism's plausibility; cite as lineage, not as covering it (they order by data properties, not corpus completeness at insertion time) |
| 18 | Mandarapu & Kunkunuru, *When to Repair a Graph ANN Index*, arXiv:2607.00728, 2026 | https://arxiv.org/abs/2607.00728 | Confirms "no automatic self-repair" as an accepted premise, but their mechanism is deletion-driven topological orphaning — dangling search paths after a delete. Explicitly does not cover semantic degradation with intact reachability; cite to draw that distinction, since it is the reason structural forensics alone would miss this |

No paper found does a direct sensitivity analysis of Algorithm 4's diversity
heuristic as a function of candidate-pool size at insertion — the specific
causal chain (partial-corpus insertion → permanently worse links, invisible to
every structural check) appears to still be ours to establish empirically.

Also relevant: Elastic's HNSW-per-segment writeup
(https://www.elastic.co/search-labs/blog/hnsw-graph), Pinecone observability
(https://www.pinecone.io/blog/open-source-monitoring-stack/), Milvus #37703
(https://github.com/milvus-io/milvus/issues/37703).

---

## Framing risks — what NOT to claim

This is the load-bearing section. The README and any write-up get checked
against it.

1. **Do not claim to be first to notice ANN has no test oracle.**
   arXiv:2502.20812 says it in the abstract. Claim instead: first to show the
   oracle problem *has teeth under replication and failure*, with a measurement.

2. **Do not claim "nobody chaos-tests vector databases."** Milvus ships a Chaos
   Mesh suite and files bugs from it. Claim: *their assertions are liveness and
   entity counts, never recall.*

3. **Do not claim "recall degradation in vector DBs is unknown."** It is blogged
   extensively and studied under churn and corpus growth. Claim: degradation
   *attributable to replica-level failure damage, persisting after recovery*, is
   unmeasured.

4. **Do not claim `leave_one_out_agreement` as a new detector.** Jackknife
   ensemble disagreement is standard, and `1/Ratio@k` already gives a
   low-overhead ANN quality signal (not actually ground-truth-free — it still
   needs true-neighbour distances; see the must-cite table entry). Claim the
   replica-identity attribution argument plus the 1e-4 nondeterminism control.

5. **Do not claim "no vector database repairs missing data."** Weaviate 1.29
   ships real hash-tree anti-entropy; Vespa reconciles by timestamped-document
   checksum. **The 0% healing result is scoped to nano-db**, which has no
   anti-entropy by design — `cluster/coordinator_main.cpp` already documents that
   element-count comparison is "a scalar proxy, not a true diff" and that real
   reconciliation is a separate effort. So the claim is not *"systems fail to
   repair"* but ***"there is no signal that would tell you to repair"***, and the
   reason the exact-store anti-entropy that does exist cannot be pointed at the
   index is §4: two correct HNSW graphs over identical data differ bit-for-bit.
   Stated as a universal law, one reviewer with Weaviate experience sinks this.

6. **Do not claim all such failures are silent.** Milvus #37703 errored loudly.
   The claim is *"approximation permits silence, and the silent variants are the
   ones that go unfixed"* — this project's own `miss@stop == miss@end` result is
   the load-bearing exhibit here now, not a borrowed production issue. No public
   report found *quantifies* a silent recall divergence the way this project's
   own measurement does; say that plainly rather than reaching for a citation
   that doesn't hold up (see §6 above on ClickHouse #104674).

7. **Watch the title collision** with arXiv:2511.04221 ("Convergent ANN Search").
   Distinguish explicitly.

8. **n=5 will get probed.** Lead with effect sizes and per-run data, not the
   p-value — 0.0079 is the exact floor for 5v5, so it carries less information
   than it looks like it does. The direct mitigation is `--dist sift`: an
   independent replication on real data is worth more than another synthetic seed.

---

## Where this could go

Recorded for reference; writing the paper is not currently in scope.

| Venue | Fit | Dates |
|---|---|---|
| **PVLDB / VLDB Experiments & Analysis** | Best fit — the track rewards "we measured a thing everyone assumed and found it false" | Rolling monthly; VLDB 2027 Athens, final ~Mar 1 2027 |
| **SIGMOD 2027** | Appetite proven (SIGMOD '26 took a vector-search evaluation-methodology paper) | Four rounds: Jan 17 / Apr 17 / Jul 17 / Oct 17 |
| **EuroSys 2027** | Nearest actionable systems deadline | Spring abstracts **Sep 24 2026**, papers Oct 1 |
| **OSDI '27** | Systems framing | Abstracts Dec 1 2026 |
| **HotStorage '27** | Ideal for the 5-page provocation version | '26 deadline passed (Jun 2026) |
| **DBTest** (SIGMOD workshop) | Natural home for the harness/methodology contribution | — |

Pragmatic path: arXiv preprint plus an engineering writeup
(`docs/postmortem-recall-bugs.md` is already most of one). The area is moving
fast enough — several 2026 arXiv entries — that preprint priority matters more
than usual.
