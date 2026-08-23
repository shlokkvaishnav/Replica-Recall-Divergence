# Spec: research/cross-system-replication

**Branch:** `research/cross-system-replication`
**Status:** DRAFT — no implementation or results yet. This file is committed before either exists.

## Research question

Does the replica-level search-quality divergence observed on nano-db — measurable, statistically significant recall/completeness degradation under node-kill chaos that does not recover after full cluster recovery — also occur on a production-grade replicated vector database, under the same measurement protocol?

## Hypothesis

Some degree of `index_recall` and/or `completeness` divergence will be measurable under the same chaos protocol on the target system, because the underlying mechanism the nano-db result rests on — an HNSW-family graph is not insertion-order invariant, and two independently-built replicas of the same data are not bit-identical — is a property of the graph-ANN family, not of this specific implementation. Whether it **heals** is a separate and genuinely open question: unlike nano-db, the target system may ship real anti-entropy (e.g. Weaviate's hash-tree object replication), and per `research/RELATED_WORK.md` §4, that mechanism operates on exact object identity and is not obviously able to repair graph-level (not just data-level) divergence — but that argument has not been tested, only reasoned about.

## Null / alternative hypothesis

**Null:** replicas of the target system show no statistically significant `index_recall` or `completeness` separation between the chaos and no-chaos conditions (same Mann-Whitney design as nano-db). This would not prove the phenomenon can never occur elsewhere, but it would falsify generalization to this specific system under this specific protocol, and would localize the nano-db finding as at least partially implementation-specific rather than a general property of replicated graph-ANN systems.

**Alternative (the interesting middle case):** divergence is measurable but **heals** — i.e. the target system's own repair mechanism (if any) restores search quality after recovery, unlike nano-db. This would not falsify the divergence mechanism, but it would falsify the generalization of the *non-healing* half of the nano-db result, and would be a genuinely important finding on its own: it would show nano-db's "0% of lost vectors come back" result is a consequence of nano-db having no anti-entropy by design, not a property graph-ANN replication in general.

## Motivation

Per `research/README.md`'s experiment index and the top-level README's "Open research questions," this is the highest-priority remaining item — the project's own documentation already states that pointing this harness at a second real system "is the step that would turn a measurement of one toy system into a contribution." Every result to date is n=1 at the system level; this branch is the first attempt to move past that.

## Experimental design

**Target system:** Qdrant, chosen over Weaviate/Milvus for this first attempt because it exposes a gRPC API (closer to nano-db's own probe design, which calls `ShardService.Search` / `ListLocalIds` directly per replica) and ships a well-documented Docker Compose distributed deployment. Weaviate is the natural second target afterward specifically *because* it ships real hash-tree anti-entropy (§4 of `RELATED_WORK.md`) — testing it would directly probe the alternative hypothesis above. Not both in this branch; one system per branch, per `GIT_WORKFLOW.md`'s isolation rule (system identity is exactly the kind of variable that should not be mixed with anything else in one run).

**Topology:** match nano-db's 2-shard × 3-replica topology as closely as Qdrant's sharding/replication configuration allows. Document any topology parameter that cannot be matched exactly (e.g. Qdrant's shard/replica semantics may not map one-to-one onto nano-db's) rather than silently approximating it.

**Dataset:** SIFT1M, identical to the nano-db experiment (`research/replica_recall/sift.py`'s loader, same scaling) — corpus is the one variable this branch must **not** change, since `RELATED_WORK.md`'s own evidence shows corpus choice alone can hide or reveal the effect (uniform vs. SIFT1M on nano-db, p = 0.31 vs. p = 0.0079).

**Fault model:** node-kill chaos, matched as closely as possible to `chaos_harness.py`'s protocol (random SIGKILL + restart of replica processes/containers, confirmed-write tracking, a settling window before scoring). Qdrant's process/container lifecycle differs from nano-db's bare-process model (Docker container kill vs. direct SIGKILL) — document the difference rather than treating them as identical.

**Metrics:** reuse `research/replica_recall/metrics.py`'s measurement core unmodified — `index_recall`, `completeness`, `e2e_recall`, `agreement`, `leave_one_out_agreement` — computed via a **new, Qdrant-specific probe module** (analogous to `probe.py`) that queries each replica directly. The metric definitions themselves must not change between systems; only the transport/API adapter that feeds them does. This is the load-bearing design constraint of the whole branch: if the metrics changed too, a difference between systems would be uninterpretable (confound between "different system" and "different measurement").

**Protocol:** baseline-first (no-chaos noise floor before the chaos condition, exactly as nano-db's README requires), seed sweep (start at 5 seeds to match the existing nano-db result directly; consider more once the pipeline is validated — see `DECISION_LOG.md`'s entry on the Mann-Whitney floor), quiesce/healing protocol (stop chaos, keep watching, score on absolute missing-write count per the "dilution trap" decision, not on a ratio).

## Metrics that decide the outcome

Same four as nano-db. The comparisons that matter: (1) chaos vs. baseline `index_recall`/`completeness` separation (exact Mann-Whitney, matching nano-db's test), (2) missing-write count at chaos-stop vs. end-of-observation-window (healing test), (3) `loo_agreement`'s hit rate vs. chance, matching Q4 of nano-db's `analyze.py`.

## Baselines / controls

No-chaos run on the same corpus and topology, established before the chaos condition — required before any chaos-condition number can be interpreted, per the nano-db protocol. If time permits, repeat the uniform-vs-SIFT1M sanity check (`--dist uniform` equivalent) to confirm the target system's measurement isn't sitting in the same distance-concentration trap nano-db's early iterations were.

## Expected outcomes

(a) **Divergence detected, does not heal** — matches the nano-db result closely; strengthens the generalization claim, though still only n=2 systems.
(b) **Divergence detected, heals** — confirms the alternative hypothesis; shows nano-db's non-healing result is a consequence of its no-anti-entropy design choice, not evidence about graph-ANN replication generally. Still a positive, useful finding.
(c) **No measurable divergence** — falsifies generalization to this system under this protocol. Requires investigating why before drawing conclusions (topology mismatch? Qdrant's segment-merge/optimizer behavior masking it? insufficient chaos intensity?) rather than treating the null result as final on the first attempt.
(d) **Result confounded** (e.g. by an unmatched topology parameter, a Qdrant version-specific behavior, or the fault model not translating cleanly to Docker container semantics) — DECISION should be REPRODUCE, not MERGE or ABANDON, per `GIT_WORKFLOW.md`.

## Interpretation plan

Outcome (a) supports — but does not prove — that the mechanism is general; it remains n=2. Outcome (b) is arguably the most scientifically interesting because it's the one this project cannot currently distinguish from (a) without running it. Outcome (c) does not mean "the nano-db result was wrong" — it means the effect, if real, is either implementation-specific or requires conditions this run didn't reproduce; both are worth stating precisely rather than collapsing into a vague "didn't replicate." Outcome (d) means this branch's decision is REPRODUCE, and the confound gets documented before any interpretation is drawn.

## Confounds considered

- **Fault-model mismatch.** SIGKILL vs. `docker kill` vs. Qdrant's own graceful-shutdown handling are not the same failure mode. If Qdrant handles the induced fault more gracefully than nano-db does, a null result could reflect a weaker fault, not a healthier system.
- **Topology mismatch.** Qdrant's shard/replica configuration model may not map exactly onto 2×3; approximating it could change the result for reasons unrelated to the phenomenon under study.
- **Version and configuration drift.** Qdrant's own HNSW parameters (`m`, `ef_construct`, `ef`) are not nano-db's; matching them approximately is reasonable, matching them exactly is not required for the *general* question, but the values used must be recorded (per §18 of the standing instructions on cross-system experiments: system version, config, replication mechanism, index implementation, failure model, dataset, query workload, evaluation protocol, recovery behavior, metrics, and differences from nano-db all get documented, not just the headline result).
- **Optimizer/merge behavior.** Qdrant periodically merges/optimizes segments in the background; this could either mask divergence (if it silently repairs something) or manufacture the appearance of "healing" that isn't actually anti-entropy of the kind this project cares about. Needs to be identified and reported on explicitly, not averaged over.
- **Measurement-core drift.** Mitigated by design — `metrics.py` is reused unmodified. If a Qdrant-specific quirk seems to require changing the metric definitions, that change does not belong on this branch (see `GIT_WORKFLOW.md`'s isolation rule) and should be its own `method/*` branch, evaluated on its own, before being applied here.

---

## Addendum: 2026-08-23 — confirmed live per-replica probe path exists

Before writing any implementation code, the open finding noted when this branch was picked back up — that Qdrant exposes an undocumented internal gRPC service (`PointsInternal`, default port 6335, on the same address as the `--uri` cluster-consensus endpoint) with a `shard_id`-scoped `CoreSearchBatch` RPC — was verified empirically rather than taken on faith. Verification method and result:

1. Brought up a 3-node Qdrant cluster (`qdrant/qdrant:latest`, distributed mode via `QDRANT__CLUSTER__ENABLED=true`, each node's `--uri` on its own `:6335`) and created a collection with `shard_number=2, replication_factor=3` — the same 2-shard × 3-replica topology this branch's experimental design calls for.
2. Qdrant's public Python client (`qdrant-client` on PyPI) only ships `.proto` files for the external API (`points.proto`, `collections.proto`, `points_service.proto` — port 6334/6333). It does **not** ship `points_internal_service.proto`, `raft_service.proto`, or `collections_internal_service.proto` — confirming these are genuinely undocumented from the client's perspective, not just under-advertised.
3. Pulled the internal `.proto` files directly from the `qdrant/qdrant` GitHub source (`lib/api/src/grpc/proto/`), compiled Python gRPC stubs from them, and called `PointsInternal.CoreSearchBatch` directly against port 6335 from outside the cluster's own container network, with no credentials of any kind.
4. Confirmed gRPC reflection is *not* enabled on 6335 (`UNIMPLEMENTED` on `ServerReflectionInfo`, vs. connection failure — i.e., a gRPC server is there, it just doesn't self-describe), so the undocumented-ness is real: nothing short of reading the Rust source (or, as done here, the checked-in `.proto` files) tells a client this surface exists.
5. `CoreSearchBatch` against `shard_id=0` on a healthy collection returned a normal (empty, since no points existed yet) result — **no authentication or metadata was required**. Requesting a shard the node does not hold (`shard_id=99`) returned `NOT_FOUND: shard 99 not found` rather than a redirect or a coordinator-side scatter-gather — confirming the RPC is answered locally, per-node, per-shard, not routed.
6. Inserted two points (landing on different shards under the collection's default hashing) via the normal public API, then queried `CoreSearchBatch` for both shards directly against all three nodes' internal ports individually. Every node answered identically and correctly for the shards it holds — i.e., this is a genuine **direct-to-replica** read path, architecturally the same shape as nano-db's own `ShardService.Search`: no quorum, no scatter-gather, one specific replica's own view of one specific shard.

**Conclusion: the finding is confirmed.** A live, per-replica probe (not just snapshot-after-stop, e.g. via file-level storage inspection or a stopped-node's on-disk state) is buildable for Qdrant using exactly this path — `PointsInternal.CoreSearchBatch` for the index-quality side of the measurement, and `PointsInternal.Scroll` (also `shard_id`-scoped in the same `.proto`) for enumerating each replica's own live id set, which is the `ListLocalIds` analog nano-db's `probe.py` depends on. This is what makes reusing `metrics.py` unmodified possible on this branch, per the isolation constraint in the Experimental design section above: the transport/API adapter differs, the measurement core does not.

This also means the probe **does not need Qdrant's own consistency/read-preference settings to cooperate** — it bypasses them entirely, the same way `ShardService.Search` does on nano-db, which is what keeps the two systems' probes comparable rather than measuring two different things.

One caveat worth recording rather than glossing over: this port is intended as private, cluster-internal transport (it shares a port with Raft consensus messages), not a supported client surface. Qdrant could change or remove it without notice in a future release, is not guaranteed to behave identically across versions, and none of this is sanctioned or documented usage of the product. `qdrant_probe.py` (added on this branch) pins and records the exact image tag/version this was verified against for that reason.

## Results

*(Not yet — no implementation exists on this branch. This section stays empty until an experiment actually runs.)*

## Interpretation

*(Not yet.)*

## Decision

*(Not yet — DRAFT until results exist. See `GIT_WORKFLOW.md`'s merge criteria before deciding.)*
