# A Weaviate per-replica probe you can compute a number with

Issue #46 · branch `method/weaviate-probe-per-id` (from #44's head) · the two prerequisites PR #44 named before its probe is used for a published result.

**Both done.**

- **Per-id presence.** Each object carries its id as **16 bytes big-endian** in the internal API's payload, so presence is now a *set*, not a byte count. `objects_present_ids(node, shard, ids) -> (ok, set)`. Validated 8/8 against a constructed expectation — including **0 false positives on never-written ids**, the check that separates a working decoder from one that returns whatever it was asked for. Decisive case: a restarted replica reported **0 of 10 peer-only ids while holding 20 of 20 always-written ids, with all peers up**.
- **Digest pin.** `weaviate@sha256:4d2eceef…`, with the command to re-derive it in the source. Pinning makes runs reproducible against one build; it does not make the undocumented internal API a contract.

## The trap this branch fell into, and now blocks

`create_class` returned **422 "already exists"** and the harness carried on. The pre-existing class — left by Weaviate's auto-schema — was `replicationConfig.factor 1` with **3 shards**: sharded across the nodes, not replicated onto them. Every node reported a different shard name, a single-id request returned 0 bytes because the object lived elsewhere, and for ten minutes the cluster was not the topology the whole per-replica question presumes.

`create_class` now calls `verify_class()` on 422 (factor 3, exactly one shard) and fails loudly on a mismatch. **#43's result is unaffected** — its transcripts show one shared shard name across all three nodes.

That is the third instrument in this project to report success while measuring something else: #26 (stale output looking current), #38 (a chaos loop dying silently), and now this.

## Reproducing

```bash
python -c "import sys;sys.path.insert(0,'research/weaviate_probe');import weaviate_topology as t;t.write_compose_file()"
docker compose -p rrd-weaviate -f research/weaviate_probe/weaviate_run/docker-compose.yml up -d
python -c "import sys;sys.path.insert(0,'research/weaviate_probe');import weaviate_topology as t;print(t.create_class(0)); print(t.verify_class(0))"
python research/weaviate_probe_per_id/per_id.py     # 8/8, ~2 min, stops and restarts node2
docker compose -p rrd-weaviate -f research/weaviate_probe/weaviate_run/docker-compose.yml down -v
```

Weaviate 1.29.0 (digest-pinned), 1 shard × 3 replicas, 128-d vectors, one host. The 16-byte id offset is an artifact of this build — which is why the pin and the decoder landed together.
