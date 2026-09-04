# Spec: method/weaviate-probe-per-id

**Branch:** `method/weaviate-probe-per-id` (from #44's head; stacked behind PR #45 until that lands on `main`)
**Issue:** #46 (body copied verbatim below, per `AGENT_PIPELINE.md`)
**Date opened:** 2026-09-04
**Status:** COMPLETE

### Type

method (a new methodological component — metric, detector, protocol)

### Research question

Can the Weaviate per-replica probe report **which** ids a replica holds, rather than only how many bytes came back — and can it be pinned to a build, so the undocumented API it depends on cannot change underneath a result?

Both are prerequisites named in PR #44's own Decision section and raised again in its review. This issue does them together because they are one thing: the probe is not yet safe to compute a published number with.

### Hypothesis

**(a) Per-id presence is extractable without decoding Weaviate's whole object format.** The internal API returns a binary encoding of the objects requested. Rather than reverse-engineering that format, an id's presence can be decided by searching the response for that UUID's own bytes — the 16-byte big-endian form, the 36-char ASCII form, or both. Expected: at least one appears once per returned object, so presence is decidable per id and the probe's answer becomes a *set* rather than a size.

**(b) Pinning by digest is mechanical**, and `cr.weaviate.io/semitechnologies/weaviate:1.29.0` currently resolves to `sha256:4d2eceef34882b5e573ee77ef0e92423838583676f1cf0f054c186b36444b132`.

### Null / alternative hypothesis

(a) fails if ids appear in neither form — e.g. the encoding stores a hash, a compressed form, or a per-shard local index rather than the UUID — in which case per-id presence needs the real format, and the honest options are to read Weaviate's source or to keep the coarse size-based probe and say so in every result computed with it. A partial failure is likelier than a total one and must be handled explicitly: the id appears for *some* objects but not all (e.g. only for objects whose properties were set), which would make presence look id-dependent when it is encoding-dependent.

### Motivation

`completeness` is defined over specific ids — of the ids the writer confirmed, how many does this replica hold. Response size answers "how many of these came back", which is adequate for a feasibility verdict (#43) and **not** adequate for the metric: it cannot say *which* id is missing, cannot detect a substitution (one id absent, another unexpectedly present), and is only monotone under the assumption that objects are equal-sized, which holds for this harness's fixed-dimension vectors and would silently break the moment properties vary. PR #44's review said this follow-on should land before the probe computes a published number, and ranked it above the sampling work.

The digest half is smaller but blocking in a different way: the probe depends on an **undocumented** API, so "1.29.0" is not a specification of anything — a tag can be repointed, and then a re-run silently measures a different program.

### Experimental design

No experiment; instrument work on `method/weaviate-probe-per-id`, reusing #43's cluster.

1. **Pin.** Replace the tag in `weaviate_topology.py` with the digest above; record how it was obtained (`docker image inspect ... RepoDigests`) so it can be re-derived rather than trusted. Verify the cluster still comes up on the pinned reference.
2. **Characterize the encoding.** Write N objects with known UUIDs. Request a single known id; dump the response bytes; check for the UUID in 16-byte big-endian form and in ASCII. Repeat for a request of several ids to see how objects are delimited.
3. **Implement `objects_present_ids(node, shard, ids) -> set[str]`** returning the subset actually present, by whichever form step 2 found.
4. **Validate against a constructed answer, not against itself.** Ask for a deliberate mix — ids known present, ids never written, and ids written only to peers while one replica was down — and require the returned set to equal the constructed expectation exactly. This is the check that distinguishes a working decoder from one that returns everything asked for.
5. **Cross-check against the coarse probe.** For the same requests, `len(returned_set)` must move with response size, or one of the two is wrong.

### Metrics

Pass/fail on step 4: does the returned set equal the constructed expectation, for a mix containing at least one present, one absent, and one peer-only id? Secondary: whether both UUID forms appear (robustness), and whether the coarse size and the decoded count agree.

### Baselines / controls

The coarse size-based probe from #43 is the baseline and the cross-check — it is known to distinguish 50 objects (31,190 bytes) from 0. A decoder that disagrees with it on those two cases is wrong regardless of what it does in between. Control for step 4: the same request against all three replicas of a healthy cluster must return the same set.

### Expected outcomes

(a) UUIDs appear and per-id presence works → the probe returns sets, `completeness` becomes computable per-id, and the size-based path is retired to a cross-check. (b) UUIDs appear only in some objects → per-id presence is conditional; record the condition and do not use it for `completeness` until understood. (c) UUIDs do not appear at all → the size-based probe stands, and **every result computed with it must carry the equal-size assumption in writing**; reading Weaviate's source becomes a separate, deliberately-decided issue rather than something slipped into this one.

### Interpretation plan

(a) → the Weaviate experiment's `completeness` arm is ready; the remaining prerequisite is sampling faster than the ~0.3s repair observed in #43. (b)/(c) → the experiment can still run with the coarse probe, but its `completeness` claim is bounded to "how many of the requested ids" rather than "which", and the README wording for the eventual Weaviate result inherits that bound. In no case does an undecoded payload get used as though it were per-id data.

### Confounds considered

**A decoder that always succeeds is the failure mode.** If presence is decided by "the id appears in the response" and the server echoes requested ids in a header, an error message, or a not-found stub, every id looks present — which is why step 4 requires ids that are genuinely absent and genuinely peer-only, and why the control requires agreement across replicas rather than merely a plausible-looking set. **Equal-size assumption:** the cross-check in step 5 is only meaningful for this harness's fixed-dimension vectors and no payload properties; that scope must be stated, not assumed to generalize. **Digest pinning changes nothing about the API's stability** — it makes results reproducible against one build; it does not make the undocumented endpoint a contract, and the spec must not imply otherwise.

### Before submitting

- [x] I checked README.md's "Open research questions" and research/DECISION_LOG.md and this isn't a duplicate or already-ruled-out question.
- [x] This is one answerable question, not a broad restatement of the whole research thesis.


---

## Results

Live 3-node Weaviate on the digest-pinned image, class recreated to the intended config (`replicationConfig.factor 3`, `asyncEnabled true`, 1 shard — all three nodes naming the same shard). Script: `per_id.py`, which is both the implementation and its validation.

### (b) Pinned

`WEAVIATE_IMAGE` is now `cr.weaviate.io/semitechnologies/weaviate@sha256:4d2eceef34882b5e573ee77ef0e92423838583676f1cf0f054c186b36444b132`, with the `docker image inspect ... RepoDigests` command in the source so it can be re-derived rather than trusted. The cluster forms and serves on the pinned reference. Recorded plainly in the code: pinning makes runs reproducible against one build; it does **not** make the undocumented internal API a stability contract.

### (a) Per-id presence — hypothesis held

Each object carries its id as **16 bytes big-endian** near the start of its record (offset 18 in a single-object response), followed by timestamps, the vector, the length-prefixed class name, and the properties JSON — e.g. `…\x09\x00RrdVector\x0c\x00\x00\x00{"vid":"v7"}`. Presence is decided by scanning the response for each requested id's 16-byte form, which is a far smaller claim than parsing the format and, unlike parsing it, is falsifiable by asking for ids that are absent.

`objects_present_ids(node, shard, ids) -> (ok, set)` returns the subset the replica holds, with `ok=False` reserved for "did not serve" so it is distinguishable from "served, holds none".

**Validation: 8/8, against a constructed expectation.**

| check | result |
|---|---|
| all three replicas agree (healthy-cluster control) | pass |
| every always-written id found (20/20 on each node) | pass |
| **no never-written id found** — the always-succeeds trap | pass, 0 false positives |
| a peer that received peer-only writes reports them (10/10) | pass |
| a **stopped** node returns `ok=False` rather than an answer | pass |
| the mixed request still finds all present and no absent ids | pass |
| size-based and decoded counts agree in direction | pass (present 12,540 B / 20 ids; absent 0 B / 0 ids) |

The decisive one is step 3: with node2 restarted and **all peers up**, its first answer held **0 of 10 peer-only ids while holding all 20 always-written ids** — divergence localized *per id*, not merely counted, and it converged moments later.

### An incidental defect, found by being bitten by it

`create_class` returned 422 ("already exists") and the harness proceeded. The pre-existing class — left by Weaviate's auto-schema on an earlier write — had `replicationConfig.factor 1` with `shardingConfig.actualCount 3`: **sharded across the nodes rather than replicated onto them**. Each node then held a different subset, every node reported a different shard name, and a single-id internal request returned 0 bytes because the object lived on another node's shard. For roughly ten minutes the cluster was not the topology this project's per-replica question presumes, and nothing said so.

Fixed: `create_class` now verifies on 422 via a new `verify_class()` (factor == 3 and exactly one shard) and returns an error naming the mismatch instead of a success. **#43's result is unaffected** — its transcripts show all three nodes reporting the *same* shard name, so that cluster was correctly replicated; this is why the shard-name agreement is now an explicit check rather than an incidental observation.

## Interpretation

Outcome (a): `completeness` is now computable per id on Weaviate, from a replica read that touches no peer. Combined with #43, the Weaviate experiment's data-axis arm is ready — the remaining prerequisite is sampling faster than the ~0.3s repair #43 measured.

**What this does not establish.** That the 16-byte id offset is stable across versions — it is an artifact of one build, which is exactly why the digest pin landed in the same branch. That the scan cannot false-positive on an id that happens to appear inside another object's vector bytes: improbable at 16 bytes and unproven; the never-written-ids check bounds it empirically at 0 false positives over 10 ids, not analytically. That the properties JSON or class name can be relied on — only the id scan is used, deliberately.

The 422 finding generalizes past Weaviate: **an idempotent-looking setup call that succeeds against a pre-existing object of unknown configuration is a silent-topology hazard**, the same shape as #26 (stale output looking current) and #38 (a chaos loop dying quietly). It is the third instance in this project of an instrument reporting success while measuring something other than what was asked for.

## Decision

**MERGE.** Both prerequisites from PR #44's Decision section are closed, the per-id decoder is validated against a constructed answer including the failure mode that would make it worthless, and a real topology hazard found on the way is fixed rather than noted.

**Follow-on:** sub-second sampling for the Weaviate probe (already named in #44), now the only remaining prerequisite before the dissociation experiment.
