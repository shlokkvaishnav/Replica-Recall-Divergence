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

**Scope of what actually ran, stated up front:** the implementation (`qdrant_topology.py`, `qdrant_probe.py`, `qdrant_docker_harness.py`, `qdrant_run_experiment.py`) is complete and validated end-to-end. What follows immediately below is this branch's original single-seed pilot (kept as-is, historical record — do not read it as the final result). **The pre-registered 5-seed sweep this pilot flagged as outstanding has since been run; see the dated addendum further down for the actual result the Decision is based on.**

**Cluster / environment actually used:** `qdrant/qdrant:latest` (pulled 2026-08-23; not yet pinned to a digest — see Decision), 3-node Docker Compose cluster, `shard_number=2, replication_factor=3` collection (`vector size=128, distance=Euclid` to match nano-db's squared-L2). Confirmed via `/cluster` and `/collections/.../cluster` that the cluster forms and all 6 (shard, node) slots reach `Active` before any run starts.

**Validation run** (`--dist uniform`, no chaos, 25s, 2 writers): all 6 replicas reachable at every one of 9 samples; `index_recall` 0.98-1.00, `completeness` 1.00, `shard_agreement`/`loo_agreement` ~0.99-1.00 throughout. This is the expected healthy-cluster baseline and confirms the probe, the metrics wiring, and the CSV/JSON output are correct before trusting any chaos result.

**Baseline pilot** (`results/samples_baseline_sift_pilot.csv`, `run_meta_baseline_sift_pilot.json`; real SIFT1M, 20,000 base vectors, no chaos, 90s, seed 20260808): `index_recall = 1.0`, `completeness = 1.0`, `shard_agreement = 1.0` on every one of 72 samples across all 6 replicas. `index_recall = 1.0` exactly is expected at this corpus size — the writer's corpus pool exhausted (all 20,000 vectors written) inside the 20s warmup, so this run establishes only that the no-chaos noise floor is clean at small scale, not a recall number comparable to nano-db's 200k-vector SIFT result.

**Chaos/quiesce pilot** (`results/samples_chaos_quiesce_pilot_seed20260808.csv`, `events_chaos_quiesce_pilot_seed20260808.json`, `run_meta_chaos_quiesce_pilot_seed20260808.json`; real SIFT1M, 100,000 base vectors, seed 20260808, pre-chaos 20s / chaos 60s / quiesce 70s, 4 writers, 46,784 vectors confirmed of 48,928 attempted): 3 chaos events (2 kills of `node1`, 1 of `node0`, one of which — around t≈85.6s — coincided with a scheduled sample landing while a node was mid-restart and all 6 probes briefly read `DOWN`/`DOWN` together, i.e. the harness correctly reports "can't measure this instant" rather than fabricating a reading). One clear divergence-and-partial-healing event: `shard-1-0` (a replica on `node1`, the node killed twice) dropped to `completeness = 0.902303` at t≈59.7s, shortly after `node1`'s first restart, then climbed monotonically across every subsequent sample -- 0.9956, 0.9960, 0.9965, 0.9969, 0.9971, 0.9974, 0.9976 -- through the end of the 70s quiesce window at t≈166s, **without reaching back to 1.0**. Every other replica that dipped during the outage windows (`shard-0-1`, `shard-1-1` while `node1` was down a second time) recovered to `completeness = 1.0` by the next sample after their node came back. No `index_recall` degradation beyond ordinary sample-to-sample noise (0.98-1.00 throughout, no visible dip correlated with the chaos events) — the signal in this pilot is entirely on the completeness/data axis, not the graph-quality axis. Zero Raft `SPLIT_BRAIN` violations across permalink `raft_checks_run` in the meta file for either run.

## Addendum: 2026-08-23 (later) — the actual 5-seed sweep

Per the reviewer's `stage:changes-requested` comment on the PR that carried the pilot above, this addendum records the pre-registered protocol actually running: 5 seeds (`20260910`-`20260914`), matched-scale baseline/chaos/quiesce triples (same 100,000-vector SIFT1M corpus, same `--duration 120` for baseline/chaos, `pre-chaos 20s / chaos 50s / quiesce 50s` for the quiesce condition), against the image now pinned to its digest (`qdrant_topology.py`'s `QDRANT_IMAGE`, per Decision item 3 below). Orchestrated with `qdrant_sweep.py` (a new file, the direct analog of `../replica_recall/sweep.py`) and analyzed with `../replica_recall/aggregate.py` — **reused completely unmodified**, since `qdrant_run_experiment.py` writes the identical `samples.csv`/`run_meta.json` schema and the same `seed<N>_<condition>` directory convention. Raw output for all 15 runs is committed under `results_sweep/`.

Two transient infrastructure failures happened during this sweep and are recorded rather than silently retried away: (1) a scripting bug on my part (an over-eager `set -e` in the orchestration wrapper, combined with an ephemeral container, discarded the first attempt's completed runs before they were copied out — no experimental data was affected, since nothing had been analyzed yet, but it cost the wall-clock time of a full rerun); (2) `qdrant_run_experiment.py` itself had a real bug this sweep surfaced: a transient `socket.TimeoutError` during cluster bring-up (talking to a node's REST API under host resource contention from running two sweeps concurrently) was not caught, so the run crashed without tearing down its Docker containers — which then broke the *next* run in the sweep by squatting on `qdrant_topology.py`'s fixed ports. Fixed in `qdrant_run_experiment.py`'s `main()` by wrapping cluster bring-up through teardown in `try`/`finally`, so any exception in that window still tears down containers. Both failed runs were then cleanly resumed (`qdrant_sweep.py` skips runs with an existing `samples.csv`) and completed without incident under the fix.

**Aggregate result** (`python research/replica_recall/aggregate.py --sweep-dir research/cross_system_replication/results_sweep`; exact 5-vs-5 Mann-Whitney, floor p=0.0079):

| metric | baseline | chaos | p |
|---|---|---|---|
| within-shard spread | 0.0004 ± 0.0005 | 0.0921 ± 0.0909 | **0.0079** |
| p95 spread | 0.0015 ± 0.0021 | 0.2784 ± 0.2132 | **0.0079** |
| `index_recall` | 0.9920 ± 0.0039 | 0.9918 ± 0.0027 | 1.0000 |
| `completeness` | 1.0000 ± 0.0000 | 0.9596 ± 0.0402 | **0.0079** |
| `e2e_recall` | 0.9997 ± 0.0005 | 0.9600 ± 0.0401 | **0.0079** |
| `loo_agreement` detector hit rate | 0.8286 ± 0.0404 (baseline, mostly tie-excluded) | 0.9667 ± 0.0745 (vs. chance 0.333) | 0.0952 |

**Healing** (quiesce condition, 5 seeds, absolute missing-id count at chaos-stop vs. run-end): 20260910 recovered 84%, 20260911 0% (only 36 missing to begin with — a near-floor case), 20260912 recovered 25%, 20260913 got *worse* (-32%, more went missing after chaos stopped than was missing when it stopped), 20260914 recovered 100%. Mean recovery 35.3% (range -32% to 100%). By `aggregate.py`'s own healed/NO criterion: **1 of 5 runs healed, 4 did not** — genuinely mixed, not a clean "heals" or "doesn't heal" result either way.

## Interpretation

**The headline finding, and it is a real cross-system difference, not a replication of nano-db's result:** `index_recall` — the graph-quality metric, isolated from data content by construction (see `metrics.py`'s module docstring) — does **not** separate between baseline and chaos on Qdrant (p=1.0000, means differ by 0.0002). This is the opposite of nano-db's own established result, where `index_recall` *does* separate under chaos (per `../replica_recall/RESULTS.md`'s Verdict block, "index_recall separates -- failure damages the graph independently of what data is missing"). `completeness` and `e2e_recall` *do* separate on Qdrant, cleanly, at the same statistical floor nano-db's headline result reaches. Put together: on Qdrant, under this fault model, chaos causes replicas to diverge in **what data they hold**, but not in **the quality of the ANN graph built over what they do hold**. This directly falsifies the pilot's tentative read (SPEC.md's earlier pilot section: "no `index_recall` degradation... beyond ordinary sample-to-sample noise") as a real, now-5-seed-confirmed finding rather than a single-seed impression — good, since that pilot read turned out to be exactly right, but it is important that this addendum is not just repeating the pilot's claim with more confidence; it is an independent confirmation at the pre-registered N.

This maps onto the original Hypothesis/Null-hypothesis framing precisely at the point that framing anticipated being genuinely hard to call: the Hypothesis predicted *some* divergence because "an HNSW-family graph is not insertion-order invariant" is a property of the family, not of nano-db specifically — and no `index_recall` divergence here is evidence *against* that specific mechanism being active on Qdrant, not evidence against divergence generally, since `completeness`/`e2e_recall` divergence is real and large. This is closest to a **partial-null**: the null holds for the graph-quality channel, the alternative (divergence happens) holds for the data-content channel. Neither the original Hypothesis nor the flat Null as stated anticipated this split cleanly — worth being explicit that the pre-registered hypothesis was under-specified on this axis, not that the result contradicts it outright.

**Healing is genuinely mixed, not resolved.** 1/5 fully healed, 1/5 had almost nothing to heal from, 2/5 partially healed, 1/5 got worse after chaos stopped. This rules out both clean stories: it is not "Qdrant always heals" (4/5 didn't fully) and not "Qdrant never heals" (1/5 did, cleanly, to 100%). The pilot's single seed (20260900, not part of this 5-seed set) showed monotonic partial healing that hadn't resolved by the end of its window — consistent with this being a real, seed-dependent phenomenon rather than pilot noise, but the *mechanism* behind the variance (chaos timing/target-node luck? optimizer timing? something else?) is unexamined — Decision item 2 below.

**What this establishes and does not.** Establishes, at the pre-registered 5-seed floor: cross-replica divergence is real and measurable on Qdrant under this fault model (spread separates cleanly); the divergence is concentrated in data completeness, not graph quality, unlike nano-db; healing is inconsistent across seeds rather than reliably present or absent. Does **not** establish: *why* index_recall doesn't separate on Qdrant (segment-merge/optimizer masking graph-level effects, as flagged in Confounds, remains unchecked — Decision item 4); *why* healing varies by seed; or anything about Weaviate or other anti-entropy-bearing systems, which remain the natural next comparison per the Motivation section.

## Decision

**REVISE**, but materially advanced from the pilot's REVISE. `GIT_WORKFLOW.md`'s evidence and experimental-validity criteria are now satisfied for the core comparison — this is the actual pre-registered 5-seed sweep with a real Mann-Whitney result, not a single-seed anecdote — but two Confounds items remain genuinely open and a full MERGE/ARCHIVE call on the broader research question (does this generalize?) shouldn't be made until they're addressed:

1. ~~Run the actual 5-seed sweep~~ — **done**, this addendum.
2. **New, from this sweep's own healing result:** investigate why healing outcome varies so much by seed (100% down to -32%) — check whether it correlates with which node/shard was targeted, how much data was in flight when chaos hit, or chaos-event count/timing per seed (`events.json` per run has this).
3. ~~Pin `QDRANT_IMAGE` to a digest~~ — **done**; this sweep ran under the pinned digest throughout (unlike the pilot, which ran under `:latest`).
4. Investigate Qdrant's segment-merge/optimizer activity as a candidate explanation for why `index_recall` doesn't separate here (still open, unchanged from the pilot's Decision) — this time with 5 seeds' worth of node logs to check rather than one.
5. The implementer-side bug fixed in this addendum (`qdrant_run_experiment.py` teardown on exception) should get a reviewer's eyes on the fix itself, not just the sweep it unblocked.

Re-claim this branch or open a follow-on `experiment/*`/`analysis/*` branch for items 2 and 4 specifically — both are analysis of already-collected data (`results_sweep/`'s per-run `events.json` and node logs, not yet checked), not new experiment runs, so they may be cheaper to close out than this addendum was.
