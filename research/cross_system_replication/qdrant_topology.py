"""
Qdrant cluster topology + Docker Compose bring-up, analogous to the
topology section of chaos_harness.py.

Matches nano-db's 2-shard x 3-replica layout as closely as Qdrant's own
distributed model allows (SPEC.md's Experimental design section): one
Qdrant node per "replica" slot, a collection created with
`shard_number=NUM_SHARDS, replication_factor=REPLICAS_PER_SHARD` on that
N-node cluster. Documented mismatch: nano-db assigns one PRIMARY replica
per shard explicitly; Qdrant has no primary/secondary distinction for
reads under the default (non-consistency-locked) search path -- every
replica is a peer. This is not approximated away, it is a genuine
difference between the two systems' replication models and is recorded
here rather than in a comment buried in the harness.

QDRANT_IMAGE pins the exact tag this branch was built and validated
against -- SPEC.md's addendum on the internal gRPC probe depends on this
image's behavior and is not guaranteed to hold across versions.
"""

from __future__ import annotations

import http.client
import json
import os
import time

NUM_SHARDS = 2
REPLICAS_PER_SHARD = 3           # == number of Qdrant nodes in the cluster
COLLECTION = "cross_system_replica_recall"
VECTOR_DIM = 128

QDRANT_IMAGE = (
    "qdrant/qdrant@sha256:057ee3a8da769fe7310dd3537b4dc7583bf87a95ce8ac43c0af5a46bc580d1fc"
)  # the ":latest" tag current as of 2026-08-23, pinned to its digest so every
   # run on this branch (including the pilots in results/) used the identical
   # build -- see SPEC.md Decision item 3.

HTTP_BASE_PORT = 16333      # node i's REST API:  16333 + i
GRPC_BASE_PORT = 16350      # node i's external gRPC (public API, unused by
                            # the probe -- kept for parity/debugging)
INTERNAL_BASE_PORT = 16370  # node i's internal PointsInternal/Raft port --
                            # this is the port qdrant_probe.py talks to.
# Bases spaced >= REPLICAS_PER_SHARD apart so no two (base + node) host
# ports can collide across the three port families for any node index.

ROOT = os.path.dirname(os.path.abspath(__file__))
RUN_DIR = os.path.join(ROOT, "qdrant_run")
COMPOSE_PATH = os.path.join(RUN_DIR, "docker-compose.yml")
PROJECT = "rrd-qdrant"


def http_port(node: int) -> int:
    return HTTP_BASE_PORT + node


def grpc_port(node: int) -> int:
    return GRPC_BASE_PORT + node


def internal_port(node: int) -> int:
    return INTERNAL_BASE_PORT + node


def probe_port_fn(shard_id: int, replica_id: int) -> int:
    """qdrant_probe.build_probes' port_fn. Every shard a node holds is
    served on that node's single internal port, so shard_id is ignored --
    the shard is selected by the `shard_id` field in the RPC request, not
    by which port is dialed. See qdrant_probe.py's module docstring."""
    del shard_id
    return internal_port(replica_id)


def container_name(node: int) -> str:
    return f"{PROJECT}-node{node}"


def node_service_name(node: int) -> str:
    return f"qdrant-node{node}"


def write_compose_file() -> None:
    os.makedirs(RUN_DIR, exist_ok=True)
    services = {}
    for n in range(REPLICAS_PER_SHARD):
        svc = node_service_name(n)
        cmd = ["./qdrant", "--uri", f"http://{svc}:6335"]
        if n > 0:
            cmd = ["./qdrant", "--bootstrap",
                  f"http://{node_service_name(0)}:6335", "--uri", f"http://{svc}:6335"]
        services[svc] = {
            "image": QDRANT_IMAGE,
            "container_name": container_name(n),
            "environment": ["QDRANT__CLUSTER__ENABLED=true"],
            "command": cmd,
            "ports": [
                f"{http_port(n)}:6333",
                f"{grpc_port(n)}:6334",
                f"{internal_port(n)}:6335",
            ],
            "volumes": [f"{PROJECT}-data{n}:/qdrant/storage"],
        }
        if n > 0:
            services[svc]["depends_on"] = [node_service_name(0)]

    compose = {
        "services": services,
        "volumes": {f"{PROJECT}-data{n}": None for n in range(REPLICAS_PER_SHARD)},
    }
    _write_yaml(COMPOSE_PATH, compose)


def _write_yaml(path: str, compose: dict) -> None:
    """Hand-rolled minimal YAML emitter -- avoids adding a PyYAML dependency
    for a document with a fixed, known shape. Not a general YAML writer."""
    lines = ["services:"]
    for svc, cfg in compose["services"].items():
        lines.append(f"  {svc}:")
        lines.append(f"    image: {cfg['image']}")
        lines.append(f"    container_name: {cfg['container_name']}")
        lines.append("    environment:")
        for e in cfg["environment"]:
            lines.append(f"      - {e}")
        lines.append("    command: [" + ", ".join(f'"{c}"' for c in cfg["command"]) + "]")
        lines.append("    ports:")
        for p in cfg["ports"]:
            lines.append(f'      - "{p}"')
        lines.append("    volumes:")
        for v in cfg["volumes"]:
            lines.append(f"      - {v}")
        if "depends_on" in cfg:
            lines.append("    depends_on:")
            for d in cfg["depends_on"]:
                lines.append(f"      - {d}")
    lines.append("volumes:")
    for v in compose["volumes"]:
        lines.append(f"  {v}:")
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")


# ---------------------------------------------------------------------------
# HTTP helpers (REST API, port 6333-equivalent)
# ---------------------------------------------------------------------------

def http_request(port: int, method: str, path: str, body=None, timeout: float = 2.0):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    try:
        headers = {"Content-Type": "application/json"} if body is not None else {}
        data = json.dumps(body) if body is not None else None
        conn.request(method, path, body=data, headers=headers)
        resp = conn.getresponse()
        raw = resp.read()
        try:
            parsed = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            parsed = {}
        return resp.status, parsed
    finally:
        conn.close()


def wait_for_nodes_ready(node_ids, timeout_s: float = 60) -> bool:
    deadline = time.time() + timeout_s
    pending = set(node_ids)
    while time.time() < deadline and pending:
        for n in list(pending):
            try:
                status, _ = http_request(http_port(n), "GET", "/readyz", timeout=1.0)
                if status == 200:
                    pending.discard(n)
            except Exception:
                pass
        if pending:
            time.sleep(1.0)
    return not pending


def wait_for_cluster_formed(node_ids, timeout_s: float = 60) -> bool:
    """All nodes report `status: enabled` and see len(node_ids) peers."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        ok = True
        for n in node_ids:
            try:
                status, body = http_request(http_port(n), "GET", "/cluster", timeout=1.0)
                res = body.get("result", {})
                if status != 200 or res.get("status") != "enabled" \
                        or len(res.get("peers", {})) < len(node_ids):
                    ok = False
                    break
            except Exception:
                ok = False
                break
        if ok:
            return True
        time.sleep(1.0)
    return False


def create_collection(node: int = 0, indexing_threshold_kb: int | None = None) -> bool:
    body = {
        "vectors": {"size": VECTOR_DIM, "distance": "Euclid"},
        "shard_number": NUM_SHARDS,
        "replication_factor": REPLICAS_PER_SHARD,
        "write_consistency_factor": 1,
    }
    # Issue #28: Qdrant only builds HNSW for a segment once it exceeds
    # optimizers_config.indexing_threshold (KB; Qdrant's default is 20000,
    # ~40k 128-d float vectors). Left unset unless asked, so every existing
    # run keeps Qdrant's default and the parameter shows up in run_meta.json
    # only when a run deliberately changed it.
    if indexing_threshold_kb is not None:
        body["optimizers_config"] = {"indexing_threshold": int(indexing_threshold_kb)}
    status, _ = http_request(
        http_port(node), "PUT", f"/collections/{COLLECTION}", body, timeout=10.0,
    )
    return status == 200


def wait_for_shards_active(node_ids, timeout_s: float = 60) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            status, body = http_request(
                http_port(node_ids[0]), "GET",
                f"/collections/{COLLECTION}/cluster", timeout=2.0)
            res = body.get("result", {})
            local = res.get("local_shards", [])
            remote = res.get("remote_shards", [])
            total = len(local) + len(remote)
            expected = NUM_SHARDS * REPLICAS_PER_SHARD
            if status == 200 and total >= expected and all(
                    s.get("state") == "Active" for s in local + remote):
                return True
        except Exception:
            pass
        time.sleep(1.0)
    return False
