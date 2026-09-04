# Spec: method/weaviate-probe

**Branch:** `method/weaviate-probe`
**Issue:** #41 (body copied verbatim below, per `AGENT_PIPELINE.md`)
**Date opened:** 2026-09-04
**Status:** COMPLETE

### Type

method (a new methodological component — metric, detector, protocol)

### Research question

Can this project's per-replica measurement protocol be applied to Weaviate at all — that is, is there any way to read what **one specific replica** holds and how it answers a search, without the coordinator merging replicas first? And if so, at what cost to the fault model?

This is the instrument step for README open question #1's remaining leg (Weaviate), in the same relation to it as #28 was to #30. **Its first deliverable is a feasibility verdict, not a harness.**

### Hypothesis

At least one of three paths yields per-replica reads, in descending order of fidelity to the existing protocol:

- **(a) Isolation probing.** Stop all but one replica of a shard, query with `consistency_level=ONE`, and the surviving node must answer from its own state. Highest fidelity to `probe.py`/`qdrant_probe.py` (a real search over one replica's own index), but it perturbs the cluster to take the measurement, which no previous probe in this project does.
- **(b) `/v1/nodes` per-shard object counts.** Gives a `completeness`-like quantity per replica with no perturbation, but **no search**, so `index_recall` — the metric that carries this project's finding — is not obtainable this way.
- **(c) Offline per-node forensics.** Tear the cluster down and read each node's data directory, the `graph_forensics.py` precedent (`DECISION_LOG`: "never read graph-forensics data off a live cluster"). Post-hoc only; no time series.

Expected: (a) works and becomes the probe, (b) becomes a cheap cross-check, (c) stays in reserve.

### Null / alternative hypothesis

**None of the three yields per-replica search quality**, and Weaviate cannot be measured by this protocol without modifying Weaviate. Concretely that means: (a) is refused or silently coordinator-served even with peers down (the node proxies rather than answering locally, or refuses below quorum regardless of `ONE`); (b) reports only cluster-level or class-level counts; (c) the on-disk index is not readable without Weaviate's own code. **That outcome is a finding, not a failure** — it would say the cross-system claim cannot be extended to Weaviate by this method, and the README would have to say so instead of leaving the leg open indefinitely.

### Motivation

`RELATED_WORK.md` §9 and the do-not-claim list make Weaviate the sharpest test this project has: it ships **real hash-tree anti-entropy over objects** (v1.29+, `ASYNC_REPLICATION_HASHTREE_HEIGHT`) and **nothing that repairs an HNSW graph**. So the prediction that would make this project a claim about the field rather than about two systems is: on Weaviate, `completeness` heals (async replication repairs it) while `index_recall` diverges and does not — a *dissociation between the two axes within one system*, which neither nano-db (no repair at all) nor Qdrant (both axes transient) can show. None of that is testable without a per-replica probe, which is why this issue comes first and alone.

### Experimental design

No experiment; an instrument feasibility study on branch `method/weaviate-probe`.

1. **Documentation pass, recorded.** Weaviate's replication-architecture/consistency docs describe reads as coordinator-routed at `ONE`/`QUORUM`/`ALL` with **no node-targeting parameter** (fetched 2026-09-04; quoted in the branch's notes). The API reference is JS-rendered and was not readable by fetch — so the paths below must be checked against a **running cluster**, not against docs.
2. **Cluster.** 3 nodes via `docker compose` mirroring `qdrant_topology.py`'s shape as closely as Weaviate's config allows: one class, `replicationConfig.factor = 3`, one shard if multi-shard complicates node placement (the topology need not match Qdrant's 2×3 for a feasibility verdict; it must be recorded).
3. **Path (a).** Write N objects; confirm all 3 replicas report the count via `/v1/nodes`; `docker stop` two nodes; query the survivor with `consistency_level=ONE`; check the response is served and whether it reflects that node's own state. Repeat targeting each node. **Decisive check:** with peers down, delete an object *directly on disk or via a targeted write while a peer is down*, restart, and see whether the survivor's answer differs from its peers' — i.e. whether the probe can see divergence at all, which is the entire point.
4. **Path (b).** `GET /v1/nodes` (and `?output=verbose` if it exists) on each node; record the exact schema.
5. **Path (c).** Only if (a) and (b) both fail; record what a node's data directory contains.
6. **Deliverable.** `SPEC.md` with a verdict per path, the recorded evidence, and either a working `weaviate_probe.py` + `weaviate_topology.py` (if (a) works) or a written finding that the protocol does not transfer, with what would have to change.

### Metrics

Feasibility, not measurement: for each path, **works / does not work**, with the request and response recorded verbatim. If (a) works, one further number — whether a deliberately-diverged replica reads differently from a healthy one — because a probe that cannot see known divergence is not a probe.

### Baselines / controls

The known-good analogue is `qdrant_probe.py`'s direct per-replica gRPC path (`CoreSearchBatch` with `shard_id`), which is what "works" looks like. A control for path (a): the same query against a healthy 3-node cluster must return the same result from each isolated node, or the isolation itself is changing the answer.

### Expected outcomes

(a) Isolation probing works → build the probe, file the experiment issue. (b) Only `/v1/nodes` works → a completeness-only leg is possible; `index_recall` is not, and the experiment issue must be scoped to the data axis alone, which does **not** test the dissociation this leg exists for. (c) Only offline forensics works → a post-hoc single-snapshot design, no time series, no healing question. (d) Nothing works → record that the protocol does not transfer, and the README's Weaviate step becomes "blocked, with reasons" rather than "next". (e) Isolation works but changes the measured quantity (the survivor answers differently when its peers are down, control fails) → the probe is invalid and this is (d) with more detail.

### Interpretation plan

(a) → experiment issue for the dissociation prediction. (b)/(c) → a narrower experiment issue, with the README's Weaviate wording corrected to what is actually testable. (d)/(e) → `DECISION_LOG` entry, README open question #1 rewritten to say the Weaviate leg is blocked and why, and the field-level claim in `RELATED_WORK.md` stays a motivating intuition rather than something this project can test. In no case does the absence of a Weaviate result get left implicit.

### Confounds considered

**The isolation path perturbs what it measures** — stopping two of three replicas is itself a fault, and a survivor answering under degraded quorum may behave differently from one in a healthy cluster; the control in Baselines is what detects this, and if it fires, path (a) is invalid however convenient it is. **Version sensitivity:** async replication is GA in v1.29+, so the image must be digest-pinned as `qdrant_topology.py` does, and the version recorded — a feasibility verdict on one version is not one on another. **Reading a coordinator response as a replica response** is the exact error PR #6 made in a different form (measuring something other than what the metric names); every response recorded must be checkable as having been served locally, and if that cannot be established, path (a) fails on those grounds alone. **Scope creep:** this issue must not turn into the experiment; if path (a) works, the harness beyond a probe belongs to the experiment issue.

### Before submitting

- [x] I checked README.md's "Open research questions" and research/DECISION_LOG.md and this isn't a duplicate or already-ruled-out question.
- [x] This is one answerable question, not a broad restatement of the whole research thesis.


---

## Results

Live 3-node Weaviate 1.29.0 (`gitHash 35d800d`), one class `RrdVector`, `replicationConfig.factor 3`, `asyncEnabled true`, one shard, HNSW, 128-d vectors supplied by the client. Scripts: `feasibility_check.py` (paths a and b plus the control), `divergence_check.py` (the decisive half of path a).

### Verdicts

| path | verdict |
|---|---|
| **(a) isolation probing** | **WORKS, and sees a divergence it was shown.** With two peers stopped, the survivor served both a vector search and an object list at `consistency_level=ONE`; the control passed (all three nodes returned identical top-10 on a healthy cluster, and the isolated node's answer was identical to its own healthy answer). Decisively: after writing 100 objects while node2 was down, node2 alone listed **300** while its peers held **400** — the probe reports that replica's own state, not a merged or cached view. |
| **(b) `/v1/nodes?output=verbose`** | **EXISTS BUT IS NOT TRUSTWORTHY AS A MEASUREMENT.** The per-shard `objectCount` field is real, but it lagged the truth repeatedly and by minutes: 0 on all three nodes for ≥15s after a `consistency_level=ALL` write of 300 that `Aggregate` already counted; 0 on two nodes while a third read 300; and **300 for node2 at a moment when node2's own object list returned 400**. Usable as a coarse health signal, not as `completeness`. |
| **(c) offline forensics** | Not run — (a) works, per this spec's own rule. |

### Cluster setup findings, recorded because they cost time and would cost it again

1. **Raft ports must be pinned.** With `RAFT_PORT`/`RAFT_INTERNAL_RPC_PORT` unset and `RAFT_JOIN` given as bare hostnames, a node that restarted never regained membership. After pinning both and using `node<i>:8300` in `RAFT_JOIN`, a killed node **rejoined and was ready in ~5s**, with peers up. **Caveat, against over-claiming a fix:** the `dial tcp: address 99999999: invalid port` log line still appears when a node campaigns while *all* peers are down, so that message is at least partly a symptom of unreachable peers rather than solely the misconfiguration. What is established is the behavioural difference (never rejoined → 5s), not a full diagnosis of the message.
2. **`http_request` must treat an unreachable node as a result, not an exception.** The first `divergence_check.py` run aborted with `RemoteDisconnected` because the helper only caught `HTTPError` — in a study whose method is *stopping nodes on purpose*. It now returns status `0`.
3. **Async replication does repair, once the node is healthy.** The diverged node stayed at 300 for **10 minutes** while reporting `503`/UNHEALTHY, then after a plain `docker restart` came back `200` and listed **400**. So the earlier non-convergence is not evidence against Weaviate's hash-tree anti-entropy; it is evidence that repair does not proceed while the node is unhealthy.

### The limitation that matters most for the experiment

**Isolation probing left the probed node unhealthy.** After the stop-peers / probe / restart-peers cycle, node2 served reads but reported `503` and did not recover or converge for 10 minutes, until it was restarted. A probe that damages the replica it measures cannot be run every few seconds through a chaos window, which is exactly what `sampler_loop` does on nano-db and Qdrant. **This is unresolved, and it bounds what the Weaviate experiment can be.** It was not chased further here because #41's Confounds section forbids this issue turning into the experiment.

## Interpretation

**Outcome (a): the protocol transfers, with a cost the design must absorb.** Per-replica search *is* obtainable on Weaviate — which the documentation does not describe and which the doc pass alone would have concluded was impossible. The cost is that the measurement perturbs the cluster (two replicas stopped per sample) and, as observed, can leave the probed node unhealthy.

That makes the Weaviate leg a **different experiment shape** from the Qdrant one, not the same experiment against a different image. Continuous per-sample probing through a chaos window is not currently supported; a snapshot design (converge, isolate once, measure, restore) is. The dissociation prediction — `completeness` heals via hash-tree repair while `index_recall` does not — is still testable in that shape, because it compares two metrics at the same moment rather than tracking either over time.

**What is not established.** Nothing about Weaviate's graph quality: no `index_recall` was computed here, and the probe's search path returns ids, not the scored ground-truth comparison `metrics.py` performs. Whether the unhealthy-after-isolation behaviour is inherent, a v1.29.0 bug, or an artifact of stopping *two of three* nodes (quorum loss) rather than one. Whether repair timing is measurable at all given that a probe perturbs it. One host, one version, one topology (1 shard × 3 replicas, not Qdrant's 2 × 3), n = 1 for every observation above.

## Decision

**MERGE**, as the instrument verdict #41 asked for: paths (a) and (b) answered against a live cluster with the evidence recorded, the setup traps written down, and the blocking limitation named rather than worked around.

**Follow-ons, each its own issue:**
1. `method/*` — can a replica be probed **without** stopping its peers (internal gRPC, a read against a node whose peers are network-partitioned rather than stopped, or `consistency_level=ONE` with node affinity)? This decides whether the Weaviate experiment can have a time series. It is the highest-value next step and should precede the experiment.
2. `experiment/*` — the dissociation prediction in the snapshot shape that is known to work, if (1) finds nothing better: converge, diverge deliberately, isolate once, measure `completeness` **and** `index_recall` on the same replica at the same moment.
3. `method/*` — a `weaviate_probe.py` that computes this project's metrics (scored against brute-force ground truth) rather than returning ids, reusing `metrics.py` unmodified as every other harness does.

**Not proposed:** a Weaviate sweep. Nothing here justifies one yet.
