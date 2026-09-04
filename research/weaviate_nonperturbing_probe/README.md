# Can a Weaviate replica be read without stopping its peers?

Issue #43 · branch `method/weaviate-nonperturbing-probe` (stacked on #42) · decides whether the Weaviate leg gets a time series.

**Answer: yes for object presence, no for search.**

Weaviate runs an undocumented **cluster-internal HTTP API** on `CLUSTER_DATA_BIND_PORT` — not on the main port, where every such path 404s. It is shard-scoped and per-node, and `GET /indices/{class}/shards/{shard}/objects?ids=<base64 JSON array>` returns **that replica's own objects** while every peer stays up and healthy.

| | this probe | #41's isolation probe |
|---|---|---|
| consecutive probes survived (pre-registered metric) | **20/20, 60 reads, 0 failures** | 1, then `503` for 10 min |
| perturbs the cluster | no | stops 2 of 3 replicas |
| gives `completeness` | yes | yes |
| gives `index_recall` (search) | **no** — `_search` 415s on 12 content types | yes |

**Proven local, not a coordinator in disguise** — the confound the spec called non-optional — two ways: it refuses when the node is down, and it returned **0 bytes while its peers returned 31,190** for the same ids, with all three nodes running.

**The finding that will shape the experiment:** async repair converged in **~0.3s**. An earlier attempt here polled every 2s, saw all replicas equal, and concluded "no divergence visible" — wrong, and wrong for the same reason #24 was void: sampling slower than the signal. Any Weaviate healing measurement must beat 0.3s.

Consequence: the Weaviate experiment is **asymmetric** — a `completeness` time series through the chaos window via this probe, and a snapshot `index_recall` via #41's. Full detail, the control, and what is not established: [`SPEC.md`](SPEC.md).

## What was built

- `internal_api.py` — the probe: shard lookup, binary-safe GET (the topology helper decodes UTF-8, which is how this endpoint first looked like a failure when it had succeeded), the base64-JSON `ids` encoding, presence-by-response-size, and shard status.
- `../weaviate_probe/weaviate_topology.py` — publishes the internal port (`INTERNAL_BASE`).

## Reproducing

```bash
python -c "import sys;sys.path.insert(0,'research/weaviate_probe');import weaviate_topology as t;t.write_compose_file()"
docker compose -p rrd-weaviate -f research/weaviate_probe/weaviate_run/docker-compose.yml up -d
python -c "import sys;sys.path.insert(0,'research/weaviate_probe');import weaviate_topology as t;print(t.create_class(0))"
# then drive internal_api.objects_present(node, shard, ids) -- see SPEC.md for the three checks
docker compose -p rrd-weaviate -f research/weaviate_probe/weaviate_run/docker-compose.yml down -v
```

One host, Weaviate 1.29.0, 1 shard × 3 replicas, 50 objects at 128-d. The API is undocumented and not a stability contract — pin the image by digest before depending on it.
