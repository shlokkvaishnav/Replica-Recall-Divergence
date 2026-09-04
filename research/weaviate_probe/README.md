# Can this project's per-replica protocol be applied to Weaviate?

Issue #41 · branch `method/weaviate-probe` · instrument feasibility for README open question #1's last leg.

**Answer: yes, by isolation probing, at a cost.** Stop a shard's other replicas and the survivor answers a vector search and an object list at `consistency_level=ONE` from its own state — verified by creating a divergence it had to see (isolated node lists **300**, peers hold **400**). Weaviate's docs describe no node-targeting read path, so the doc pass alone would have concluded this was impossible; it took a live cluster to find out.

**The cost, and the open problem:** the probe stops two of three replicas to take one measurement, and the probed node was left `503`/UNHEALTHY for 10 minutes until restarted. **A probe that damages what it measures cannot run every few seconds through a chaos window**, which is what the nano-db and Qdrant harnesses do. That bounds the Weaviate experiment to a snapshot shape until a cheaper per-replica read is found — the first follow-on.

Full verdicts per path, the setup traps, and what is not established: [`SPEC.md`](SPEC.md).

## What was built

- `weaviate_topology.py` — 3-node compose (Raft ports pinned; see SPEC "Cluster setup findings"), class creation with `replicationConfig.factor 3` and `asyncEnabled true`, HTTP helper that treats an unreachable node as status `0` rather than raising.
- `feasibility_check.py` — #41's paths (a) and (b) plus the control the spec requires (all three nodes must agree on a healthy cluster, or isolation is changing the answer rather than revealing it).
- `divergence_check.py` — the decisive half of path (a): create a known divergence, isolate the victim so async repair has no peer to sync from, and check the probe reports it.

## Findings worth carrying forward

| finding | detail |
|---|---|
| Per-replica reads are possible | Isolation + `consistency_level=ONE`; sees a divergence it was shown |
| `/v1/nodes` `objectCount` is **not** a measurement | Lagged by minutes; read 300 for a node whose own list returned 400 |
| Raft ports must be pinned | Unset, a restarted node never rejoined; pinned, it rejoined in ~5s |
| Async repair works — when the node is healthy | Diverged node stayed 300 for 10 min while UNHEALTHY, converged to 400 after a restart |
| Isolation leaves the node unhealthy | The open problem; bounds the experiment's shape |

## Reproducing

```bash
python -c "import sys;sys.path.insert(0,'research/weaviate_probe');import weaviate_topology as t;t.write_compose_file()"
docker compose -p rrd-weaviate -f research/weaviate_probe/weaviate_run/docker-compose.yml up -d
python research/weaviate_probe/feasibility_check.py     # paths (a) and (b), with the control
python research/weaviate_probe/divergence_check.py      # the decisive check
docker compose -p rrd-weaviate -f research/weaviate_probe/weaviate_run/docker-compose.yml down -v
```

One host, Weaviate 1.29.0, 1 shard × 3 replicas, n = 1 per observation. This is a feasibility verdict, not a measurement.
