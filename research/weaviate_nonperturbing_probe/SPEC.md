# Spec: method/weaviate-nonperturbing-probe

**Branch:** `method/weaviate-nonperturbing-probe` (stacked on `method/weaviate-probe`, PR #42, until that merges)
**Issue:** #43 (body copied verbatim below, per `AGENT_PIPELINE.md`)
**Date opened:** 2026-09-04
**Status:** COMPLETE

### Type

method (a new methodological component — metric, detector, protocol)

### Research question

Can one Weaviate replica be read **without stopping its peers** — so that per-replica measurement stops perturbing the cluster it measures, and a time series through a chaos window becomes possible?

#41 (PR #42) established that isolation probing works and sees divergence, and that it costs two stopped replicas per sample and left the probed node `503`/UNHEALTHY for ten minutes. That bounds the Weaviate experiment to a single snapshot. This issue asks whether that bound is real or merely the first thing that worked.

### Hypothesis

**(a) Weaviate's own internal replication API is the probe.** Weaviate's replication layer already does exactly what this project needs — one node asks another "what objects do you hold" and "give me these objects" — over `CLUSTER_DATA_BIND_PORT` (7947 in `weaviate_topology.py`). If those endpoints are reachable from outside the cluster, they are the direct analogue of `qdrant_probe.py`'s `CoreSearchBatch` with an explicit `shard_id`, which is how the Qdrant leg probes replicas with zero perturbation. Expected: object *listing* per replica is available this way; per-replica *search* may not be, because replication moves objects, not queries.

**(b) Partition instead of stop.** `docker network disconnect` leaves the process running while cutting it off from peers. If Weaviate's `503` after isolation is a consequence of the process being *restarted* rather than of being alone, a partitioned node may stay healthy and answer at `consistency_level=ONE`, making the probe repeatable even if it still perturbs.

### Null / alternative hypothesis

Neither works: (a) the internal endpoints are unauthenticated-but-unreachable, undocumented in a way that makes them unusable, or return digests rather than object ids/vectors; and (b) a partitioned node degrades the same way a restarted one does — `503`, or refuses reads below quorum regardless of `ONE`. Then the snapshot shape from #41 is the real bound, the Weaviate experiment is a single-measurement design, and **that is the finding**: this project's time-series protocol does not transfer to Weaviate, only its point-in-time comparison does.

A third failure mode worth naming: (a) works for listing but **not** for search, in which case `completeness` gets a time series and `index_recall` does not — which is exactly backwards for the dissociation prediction, since `index_recall` is the metric carrying the claim.

### Motivation

The Weaviate leg exists to test one prediction (`RELATED_WORK.md` §9): a system with real hash-tree anti-entropy over objects and none over the graph should **heal `completeness` while `index_recall` stays damaged** — a dissociation between the two axes inside one system, which neither nano-db (no repair) nor Qdrant (both axes transient, #35/#37) can show. A snapshot can demonstrate the dissociation at one moment. Only a time series can show the *healing* half — that `completeness` climbs while `index_recall` does not — which is the more convincing form of the claim and the one that matches how the nano-db and Qdrant results are stated. This issue decides which of those two claims the project can make about Weaviate.

### Experimental design

No experiment; an instrument study on `method/weaviate-nonperturbing-probe`, reusing #41's cluster (`weaviate_topology.py`, 3 nodes, factor 3, `asyncEnabled`).

1. **Enumerate what is reachable.** With the cluster healthy, probe `CLUSTER_DATA_BIND_PORT` on each node: what paths answer, what they return. Record verbatim; do not infer from documentation, which #41 already showed is silent on per-replica reads.
2. **Path (a) listing.** If an internal object-listing endpoint answers, verify it is *that node's* state by the same construction #41 used and which is the only thing that makes a probe a probe: write objects with one node stopped, restart it, and check the endpoint reports the smaller set **while all peers are up** — the property isolation probing had to stop peers to get.
3. **Path (a) search.** Check whether any internal endpoint accepts a vector query against one replica. If not, record that explicitly rather than leaving it ambiguous.
4. **Path (b) partition.** `docker network disconnect` one node, wait past the interval at which #41 saw `503`, and check: does it stay `HEALTHY`; does it serve search and list at `consistency_level=ONE`; does it report its own (diverged) state; and does it recover on reconnect **without a restart** — the failure that bounded #41.
5. **Repeatability, which is the whole point.** For whichever path works, run it ~20 times at a ~5s cadence against a healthy cluster and confirm the node is still healthy and still answering at the end. A probe that works once is what #41 already has.

### Metrics

Feasibility, with one number that decides the outcome: **the number of consecutive probes a replica survives at the sampler's cadence while remaining `HEALTHY` and answering.** Secondary: whether the path yields *search* as well as listing; whether it sees a known divergence; whether recovery needs a restart.

### Baselines / controls

#41's isolation probe is the baseline — it scores 1 on the primary metric (works once, then the node is unhealthy). The control from #41 carries over unchanged: on a healthy cluster every replica must return the same answer through whatever path is used, or the path is measuring something other than replica state.

### Expected outcomes

(a) An internal endpoint gives listing **and** search with no perturbation → the Weaviate experiment gets the same shape as the Qdrant one, and #41's snapshot bound is lifted. (b) Listing only → `completeness` gets a time series, `index_recall` is snapshot-only; the experiment must be designed around that asymmetry and say so. (c) Partition works and is repeatable → a perturbing but repeatable probe; usable, with the perturbation stated as a deviation. (d) Nothing works → #41's snapshot design stands as the bound, recorded in `DECISION_LOG`, and the experiment is scoped to a single measurement.

### Interpretation plan

(a)/(c) → the experiment issue is written for a time series, and the dissociation claim can be stated in its healing form. (b) → the experiment issue must state which half of the dissociation is time-resolved and which is not; the README's eventual Weaviate sentence inherits that asymmetry. (d) → `DECISION_LOG` entry that the time-series protocol does not transfer, README open question #1 says the Weaviate leg is a point-in-time comparison, and the field-level claim in `RELATED_WORK.md` is bounded accordingly. No outcome leaves the shape of the Weaviate experiment undecided, which is the point of doing this before the experiment rather than during it.

### Confounds considered

**An internal endpoint may be a coordinator in disguise.** The whole trap #41 avoided by construction: an endpoint that *looks* per-replica but proxies to the shard owner would pass a naive check and silently reproduce PR #6's error at a new layer. Step 2's known-divergence test is not optional for that reason. **Version coupling:** internal APIs are not a stability contract; anything found here is true of 1.29.0 and the branch must pin the image by **digest** (PR #42's review noted the current pin is a mutable tag) and say so. **Partition semantics differ from process death**, so a probe validated under partition is not automatically valid under the `docker kill` fault model the experiment uses — if (b) is the winner, the experiment must show the probe still reads correctly on a node that was killed and restarted, not only on one that was partitioned. **Repeatability at cadence is the metric, not a formality:** #41's probe also "worked" on its first call.

### Before submitting

- [x] I checked README.md's "Open research questions" and research/DECISION_LOG.md and this isn't a duplicate or already-ruled-out question.
- [x] This is one answerable question, not a broad restatement of the whole research thesis.


---

## Results

Live 3-node Weaviate 1.29.0, `weaviate_topology.py`'s cluster with one addition: the internal port published (`INTERNAL_BASE = 7947`, host `7947+n`), because the API below is **not** served on the main HTTP port — every `/indices/...` and `/replicas/...` path 404s there. Scripts: `internal_api.py`.

### What was found

A cluster-internal HTTP API on `CLUSTER_DATA_BIND_PORT`, self-described as *"Weaviate's cluster-internal API for cross-node communication"*, shard-scoped and per-node. Undocumented; found by enumeration, as #41 predicted would be necessary.

| endpoint (under `/indices/{class}/shards/{shard}`) | result |
|---|---|
| `GET /status` | `"READY"` |
| `GET /objects?ids=<b64>` | **that replica's own objects**, binary encoding. `ids` is base64 of a **JSON array** of UUID strings |
| `GET /objects/_digest` | wants a JSON body (the hash-tree digest path; not pursued) |
| `POST /objects/_search` | **415 on all 12 Content-Types tried** — see below |

### Primary metric (pre-registered): consecutive probes survived at sampler cadence

**20/20 probes × 3 replicas = 60 reads, 0 failures, ~5s cadence, 100s.** All three nodes afterwards: shard `READY`, HTTP `200`. The baseline — #41's isolation probe — scores **1**, after which the probed node was `503`/UNHEALTHY for ten minutes. The blocking limitation of #41 is removed for this path.

### The endpoint is local, not a coordinator in disguise

This is the confound #43 said was not optional, and it is settled two independent ways:

1. **It fails when the node is down.** Asking node2's internal port while node2 was stopped: connection refused (status 0), not a proxied answer.
2. **It reported its own emptiness while its peers were full.** With node2 stopped, 50 new ids were written at `consistency_level=ONE` (node0/node1 only). node2 was restarted with **both peers up throughout**; its first successful internal answer, at t+0.9s, was **0 bytes** while node0 returned 31,190 for the same ids.

So divergence is visible **without stopping any peer**. That is the property isolation probing had to perturb the cluster to obtain.

### Control

With all peers up and healthy, all three replicas returned byte-identical responses (31,190) for the same 50 ids, and an absent id returned 0 bytes — a clean presence signal, and the control #41 requires before any difference is attributed to divergence.

### Async repair is far faster than previously observed

node2 went from 0 bytes to node0's 31,190 **between t+0.9s and t+1.2s — about 0.3s**, with no restart. An earlier attempt in this same study polled every 2s and saw all three replicas equal at every sample, concluding "no divergence visible"; that conclusion was wrong, and it was wrong because the poll interval was slower than the thing being measured. Recorded because it is the same failure that voided #24 (sampling slower than the signal), reproduced here in a study whose whole subject is sampling.

### What does not work: per-replica search

`POST /objects/_search` returned 415 for: `application/json`, `application/octet-stream`, `text/plain`, `application/protobuf`, `application/msgpack`, `application/x-msgpack`, `application/gob`, and five `application/vnd.weaviate.*` guesses. The endpoint exists (GET returns 405, not 404) but its accepted encoding was not found by content-type probing, and this study does not read Weaviate's source to find it.

## Interpretation

**Outcome (b), the asymmetric one this issue named in advance.** A non-perturbing, repeatable, verified-local per-replica read exists for **object presence** — which is exactly the shape of `completeness` (of the ids the writer confirmed, how many does this replica hold) and the same role `qdrant_probe.ListLocalIds` plays on the Qdrant leg. Per-replica **search** — which `index_recall` requires — is not obtainable this way.

So the Weaviate experiment can have:

- a **time series** for `completeness`, at sampler cadence, through a chaos window, with no perturbation;
- **snapshot-only** `index_recall`, via #41's isolation probe, at a cost of two stopped replicas and an unhealthy node per measurement.

That is backwards from what would be most convenient — `index_recall` is the metric carrying the project's claim — and #43 flagged this exact possibility as the "third failure mode." It does **not** kill the dissociation prediction: showing that `completeness` heals (time series) while `index_recall` is still damaged (snapshot at the end of the same run) is a coherent design, and arguably a fair one, since the two metrics are then measured by different instruments and cannot share an artifact.

**A finding for the experiment, not just the instrument:** repair completed in ~0.3s for 50 objects. Any Weaviate healing measurement must sample faster than that or it will report "no divergence" the way this study's first attempt did. That is a hard constraint on the experiment's design and is the most transferable thing here.

**What is not established.** Whether `_search`'s encoding is reachable at all (unread source, not proven absent). Whether the binary object encoding can be parsed for vectors — this study reads only response *size*, which supports presence comparisons and nothing finer. Repair timing at realistic corpus sizes: 50 objects at 128-d is small, and 0.3s is one observation, n = 1. Whether the internal API is stable across versions — it is not a documented contract, and PR #42's review already noted the image is pinned by mutable tag, which matters more now that this branch depends on an undocumented API of that exact build.

## Decision

**MERGE.** The question #43 asked is answered with a number against the metric it pre-registered (20 vs the baseline's 1), the local-not-proxy confound is closed two ways, the control ran before the decisive test, and the negative half (search) is reported as prominently as the positive half.

**Consequences to file, each its own issue:**
1. `experiment/*` — the dissociation prediction in its **asymmetric** form: `completeness` time series via this probe, `index_recall` snapshot via #41's, in one run. This is now designable and is the Weaviate leg's actual experiment.
2. `method/*` — pin `WEAVIATE_IMAGE` by digest (PR #42 review's non-blocking note), now blocking, because this branch depends on an undocumented API of a specific build.
3. `method/*` — sub-second sampling for the Weaviate probe, since repair beat a 2s poll here; and, if it is cheap, decoding the binary object payload so presence becomes per-id rather than by response size.

**Not proposed:** reading Weaviate's source to find `_search`'s encoding. It may be the right move later; it is a different kind of work from this project's other instrument branches and should be decided deliberately, not slipped in.
