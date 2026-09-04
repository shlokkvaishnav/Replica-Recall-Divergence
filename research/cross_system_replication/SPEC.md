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

> ⚠️ **CORRECTED — see "Correction: the `index_recall` null was not a measurement of graph quality" at the end of this file (2026-09-02).** PR #11 showed this paragraph's central comparison was taken over a corpus that was not HNSW-indexed for most or all of the measurement window. The paragraph is left as written, per `GIT_WORKFLOW.md`'s rule against rewriting the record; read it with the correction.

**The headline finding, and it is a real cross-system difference, not a replication of nano-db's result:** `index_recall` — the graph-quality metric, isolated from data content by construction (see `metrics.py`'s module docstring) — does **not** separate between baseline and chaos on Qdrant (p=1.0000, means differ by 0.0002). This is the opposite of nano-db's own established result, where `index_recall` *does* separate under chaos (per `../replica_recall/RESULTS.md`'s Verdict block, "index_recall separates -- failure damages the graph independently of what data is missing"). `completeness` and `e2e_recall` *do* separate on Qdrant, cleanly, at the same statistical floor nano-db's headline result reaches. Put together: on Qdrant, under this fault model, chaos causes replicas to diverge in **what data they hold**, but not in **the quality of the ANN graph built over what they do hold**. This directly falsifies the pilot's tentative read (SPEC.md's earlier pilot section: "no `index_recall` degradation... beyond ordinary sample-to-sample noise") as a real, now-5-seed-confirmed finding rather than a single-seed impression — good, since that pilot read turned out to be exactly right, but it is important that this addendum is not just repeating the pilot's claim with more confidence; it is an independent confirmation at the pre-registered N.

This maps onto the original Hypothesis/Null-hypothesis framing precisely at the point that framing anticipated being genuinely hard to call: the Hypothesis predicted *some* divergence because "an HNSW-family graph is not insertion-order invariant" is a property of the family, not of nano-db specifically — and no `index_recall` divergence here is evidence *against* that specific mechanism being active on Qdrant, not evidence against divergence generally, since `completeness`/`e2e_recall` divergence is real and large. This is closest to a **partial-null**: the null holds for the graph-quality channel, the alternative (divergence happens) holds for the data-content channel. Neither the original Hypothesis nor the flat Null as stated anticipated this split cleanly — worth being explicit that the pre-registered hypothesis was under-specified on this axis, not that the result contradicts it outright.

**Healing is genuinely mixed, not resolved.** 1/5 fully healed, 1/5 had almost nothing to heal from, 2/5 partially healed, 1/5 got worse after chaos stopped. This rules out both clean stories: it is not "Qdrant always heals" (4/5 didn't fully) and not "Qdrant never heals" (1/5 did, cleanly, to 100%). The pilot's single seed (20260900, not part of this 5-seed set) showed monotonic partial healing that hadn't resolved by the end of its window — consistent with this being a real, seed-dependent phenomenon rather than pilot noise, but the *mechanism* behind the variance (chaos timing/target-node luck? optimizer timing? something else?) is unexamined — Decision item 2 below.

**What this establishes and does not.** Establishes, at the pre-registered 5-seed floor: cross-replica divergence is real and measurable on Qdrant under this fault model (spread separates cleanly); healing is inconsistent across seeds rather than reliably present or absent. ~~the divergence is concentrated in data completeness, not graph quality, unlike nano-db~~ — **struck 2026-09-02: this belonged on the other side of the line all along, and PR #11 established why; see the correction addendum.** Does **not** establish: that Qdrant's graph quality is unaffected by chaos, or that it differs from nano-db on that axis — the `index_recall` comparison behind that reading was not a measurement of graph quality (correction addendum); *why* index_recall doesn't separate on Qdrant — originally attributed here to unchecked segment-merge/optimizer masking (Decision item 4), which PR #11 has since replaced with a more basic explanation; *why* healing varies by seed; or anything about Weaviate or other anti-entropy-bearing systems, which remain the natural next comparison per the Motivation section.

## Decision

**REVISE**, but materially advanced from the pilot's REVISE. `GIT_WORKFLOW.md`'s evidence and experimental-validity criteria are now satisfied for the core comparison — this is the actual pre-registered 5-seed sweep with a real Mann-Whitney result, not a single-seed anecdote — but two Confounds items remain genuinely open and a full MERGE/ARCHIVE call on the broader research question (does this generalize?) shouldn't be made until they're addressed:

1. ~~Run the actual 5-seed sweep~~ — **done**, this addendum.
2. ~~Investigate why healing outcome varies so much by seed~~ — **checked** (see the later addendum below): narrowed to a candidate (same-node kills without a recovery window, plus elevated write-failure rate, both present in the single worst-outcome seed) but it does not explain the other four seeds cleanly. Still open as a *confirmed* mechanism — needs a purpose-built follow-up with deliberately controlled kill spacing/targeting, not more of the same random schedule at larger N.
3. ~~Pin `QDRANT_IMAGE` to a digest~~ — **done**; this sweep ran under the pinned digest throughout (unlike the pilot, which ran under `:latest`).
4. Investigate Qdrant's segment-merge/optimizer activity as a candidate explanation for why `index_recall` doesn't separate here (still open, unchanged from the pilot's Decision) — this time with 5 seeds' worth of node logs to check rather than one.
5. The implementer-side bug fixed in this addendum (`qdrant_run_experiment.py` teardown on exception) should get a reviewer's eyes on the fix itself, not just the sweep it unblocked.
6. **New:** a controlled follow-up experiment that deliberately varies inter-kill spacing and same-node-repeat rate as independent variables (rather than reading them out of `chaos_loop`'s random schedule after the fact) is the honest way to test item 2's candidate mechanism causally.

Re-claim this branch or open a follow-on `experiment/*`/`analysis/*` branch for items 2 and 4 specifically — both are analysis of already-collected data (`results_sweep/`'s per-run `events.json` and node logs, not yet checked), not new experiment runs, so they may be cheaper to close out than this addendum was.

## Addendum: 2026-08-23 (later still) — why healing varies by seed (Decision item 2)

Per Decision item 2, checked whether the 5-seed sweep's healing variance (recovery 84%, 0%, 25%, -32%, 100%) correlates with anything visible in the already-collected `events.json`/`run_meta.json` per run, without running anything new:

| seed | recovered | chaos events | same node repeated | shortest same-node recovery gap | confirmed writes | write failure rate |
|---|---|---|---|---|---|---|
| 20260910 | 84% | 3 | yes | 26.1s | 98,400 | 1.6% |
| 20260911 | 0%* | 4 | yes | 18.5s | 86,368 | 1.5% |
| 20260912 | 25% | 2 | no | n/a | 55,392 | 5.0% |
| 20260913 | -32% | 2 | yes | 13.4s | 31,904 | 11.0% |
| 20260914 | 100% | 3 | no | n/a | 42,272 | 5.3% |

\* 20260911 only had 36 missing ids to begin with (a near-floor case per `aggregate.py`'s own healing table) — its 0% is a small-numerator artifact, not evidence of anything.

**The clearest single pattern**: `20260913` — the worst outcome (got *worse* after chaos stopped) — is also the run with the shortest same-node recovery gap (node0 killed again only 13.4s after its previous restart) and by far the highest write-failure rate (11.0% vs. 1.5-5.3% elsewhere). A node hit again before it has time to catch up, combined with visibly degraded overall write throughput during the chaos window, is a plausible mechanism: it never gets a clean window to reconcile before absorbing further disruption, and if the *cluster's* write pressure was elevated (not just that node's), the missing-id count could keep growing after the chaos-stop timestamp for reasons unrelated to that specific node's own health.

**This does not hold up as a complete explanation.** `20260912` had no repeated node at all (each of its 2 kills hit a different node, with plenty of recovery time by construction) yet still only recovered 25% — the second-worst non-floor outcome. If "avoid hitting the same node twice without a recovery window" were the full story, `20260912` should have healed cleanly like `20260914` (also no repeats), and it didn't. Write-failure rate alone doesn't rank cleanly either: `20260914` (100% healed) had a *higher* failure rate (5.3%) than `20260912` (25% healed, 5.0%) — so elevated write pressure by itself isn't sufficient either.

**Conclusion: genuinely unresolved at n=5, narrowed but not confirmed.** The same-node-repeat-timing story is the best available single-variable candidate (it uniquely picks out the worst case correctly) but visibly fails to rank the other four seeds, and no other single variable checked here (event count, write-failure rate, confirmed-write volume) does better. This is not enough seeds to fit or rule out a multi-variable explanation responsibly — a purpose-built follow-up that *deliberately* varies inter-kill spacing and target-node repetition as controlled, not incidental, variables (rather than reading tea leaves from `chaos_loop`'s random schedule after the fact) is the honest way to actually answer this, not a larger version of the same random sweep. Recorded here as a narrowed hypothesis for that follow-up, not a finding.

## Correction: the `index_recall` null was not a measurement of graph quality (2026-09-02)

Filed as issue #15, per PR #11's Decision item 2, which recorded this correction
as needed and deliberately left it unwritten ("this addendum reports the second
run's evidence, it does not decide what to do with it").

**What PR #11 found.** `research/qdrant_optimizer_masking/` instrumented Qdrant's
`/collections/{name}` endpoint (`--capture-telemetry`) at this sweep's own scale
and protocol, across two seeds:

| | seed 20260920 | seed 20260921 |
|---|---|---|
| first telemetry sample with any node indexed | t=83.1s | t=158.4s (after nominal run end) |
| chaos window | t=50.3-131.9s | t=50.1-116.9s |
| `index_recall` samples taken with **zero** vectors indexed on every node | 32 of 53 (60%) | 62 of 74 (84%) |
| mean `index_recall`, unindexed bucket | 0.9951 | 0.9924 |
| mean `index_recall`, partially-indexed bucket | 0.9958 (n=21) | 0.9912 (n=12) |

For seed 20260921 the entire baseline period *and* the entire chaos window ran
with nothing indexed on any node.

**Why that invalidates the reading, not just the number.** Qdrant serves
unindexed segments by exact scan. A search over a flat segment is exact by
construction, so `index_recall` measured against it is ~1.0 whether or not chaos
damaged anything -- there is no approximate graph in the loop to damage. For most
of this sweep's measurement window, `index_recall` was therefore not measuring
the quantity it is defined to measure. A null in that window is not evidence that
the graph resisted chaos; it is the absence of a measurement.

**What the corrected claim is.** On Qdrant, under this fault model: cross-replica
divergence in *data completeness* is real and separates cleanly. `completeness`
is unaffected by this correction in the strict sense -- it involves no search at
all, so indexing state cannot bear on it. `e2e_recall`'s separation is also real,
but it survives as a **floor rather than an unaffected quantity**: it is a
retrieval metric, served by exact scan during the unindexed window, so what it
measured is the separation that data loss alone produces. A fully-indexed run
could show the same or more, never less. Whether Qdrant's
replicated HNSW graph diverges in quality under chaos is **untested**, on this
sweep and on any other. The earlier framing -- a real cross-system difference from
nano-db on the graph-quality axis -- is withdrawn in both directions: there is no
evidence Qdrant differs from nano-db here, and none that it agrees.

**What this correction does not do.** It does not show Qdrant's graph *would*
diverge once fully indexed; PR #11 says so explicitly, since its own runs never
finished indexing either. It does not touch this sweep's completeness, spread, or
healing findings. And it does not resolve the original optimizer/segment-merge
hypothesis (Decision item 4 above), which remains untested and is now a question
for a future run that indexes the corpus *before* measurement starts -- the
protocol fix PR #11's Decision item 4 describes, tracked separately as a
`method/*` change, not folded in here.

**Why the original text is struck through rather than deleted.** `GIT_WORKFLOW.md`:
history is not rewritten to make the project look more linear than it was. The
claim was made, it was wrong for a reason worth remembering, and the reason -- a
metric quietly measuring something other than what it is defined to measure,
because of a system behavior nobody had instrumented -- is more transferable than
the correction.

## Addendum: the graph-quality axis, re-measured on an indexed corpus (2026-09-04)

The 2026-09-02 correction above left the graph-quality axis **untested**. It has now been tested, in two steps that live in their own directories: `../qdrant_index_gate/` (issue #28, PR #29) built an indexing gate so a run refuses to start its baseline clock until every replica reports the corpus HNSW-indexed — which turned out to require `indexing_threshold` 1,000 KB, since at Qdrant's default the appendable segment leaves 85–93% of the corpus un-indexed indefinitely — and `../qdrant_gated_index_recall/` (issue #30, PR #31) re-ran this sweep's protocol with the gate on, five new seeds, each sample conditioned on its replica's indexed fraction.

**Result.** Worst-replica `index_recall` under chaos 0.978 vs baseline 0.990, every seed separated, exact Mann–Whitney p = 0.0079; the killed node is the worst replica in 4 of 5 runs; the loss is one replica's (e.g. 0.939 against peers at 0.986–0.997). The cluster-wide six-replica mean does **not** separate (p = 0.31). `completeness` and `e2e_recall` separate as they did here.

**What that does to this spec's reading.** The struck-through sentence in the Interpretation — "the divergence is concentrated in data completeness, not graph quality, unlike nano-db" — was wrong for a *second* reason beyond the un-indexed corpus: it was computed on the cluster mean, which dilutes a one-replica loss to 0.2 points. On an indexed corpus and at the replica level, Qdrant's graph diverges under chaos as nano-db's does. The cross-system claim is therefore: replica-level `index_recall` divergence under node-kill chaos is not specific to nano-db, and the cluster-wide mean hides it. The unit is part of the claim. Mechanism, healing (2 of 5 seeds below baseline 50s after the last kill; 4–5 samples each), and scale remain open; see `../qdrant_gated_index_recall/SPEC.md`.

## Addendum: healing, re-measured on a 180s horizon (2026-09-04)

This spec's own Interpretation called Qdrant's healing "genuinely mixed, not resolved" — 1/5 fully healed, 2/5 partially, 1/5 worse after chaos stopped — on a **50s** quiesce window, and that reading survived into `DECISION_LOG` and the README as "healing seed-inconsistent." `../qdrant_index_recall_healing/` (issue #35, PR #37) re-ran the gated protocol with a **180s** window and five new seeds. Both axes move:

- **Graph.** The replica-level `index_recall` loss established in the addendum above is a **transient of the restart**: 4 of 4 judged seeds back inside their own baseline range over the last 60s; after the last kill one seed dropped to 0.946 for a single 30s bin, one dipped 0.003 for one bin, two showed nothing beyond noise. A fifth seed is **unmeasured** — its chaos window fired zero kills (harness defect #38), so N was 5 pre-registered and 4 judged.
- **Data.** Missing ids at chaos stop 468 / 19 / 248 / 86 → **0 at end in all four**, 100% recovered. This sweep's 0–100% spread was a horizon effect, not a property of the system: at 50s the recovery was simply incomplete.

**What that does to this spec's reading.** "Healing is genuinely mixed, not resolved" is withdrawn as a *characteristic of Qdrant* and re-stated as a *characteristic of a 50s observation window*. The per-seed numbers above stand; what was wrong was reading them as the endpoint. Kept, not deleted, for the same reason as the 2026-09-02 correction: the transferable lesson is that a quiesce window is a measurement parameter, and one too short to reach an asymptote produces variance that looks like a finding.

**Still open.** Whether Qdrant's recovery is repair or replacement (the mechanism), anything beyond 180s, and the comparison that would license an architectural claim against nano-db — the two systems were observed on different horizons and axes. See `../qdrant_index_recall_healing/SPEC.md`.
