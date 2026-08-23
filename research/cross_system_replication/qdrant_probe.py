"""
Direct per-replica probe for Qdrant, analogous to replica_recall/probe.py.

Uses `PointsInternal`, an undocumented gRPC service Qdrant exposes on its
cluster-internal port (6335 by default -- the same port used for the
`--uri` Raft/consensus transport). See the 2026-08-23 addendum in SPEC.md
for how this was found and empirically confirmed: no authentication, no
`ServerReflection`, and a request scoped to a `shard_id` the connected node
does not hold returns `NOT_FOUND` rather than being routed -- i.e. every
call here talks to exactly one physical node's own copy of exactly one
shard, with no scatter-gather and no quorum. That is what makes it
architecturally comparable to nano-db's `ShardService.Search` probe: this
module is the transport/API adapter the isolation rule in SPEC.md's
Experimental design section requires, feeding the same, unmodified
`metrics.py`.

Qdrant has no "replica" identity distinct from "peer that holds a copy of
this shard" -- with `shard_number=N` and `replication_factor=R` on an
R-node cluster, every node holds one copy of every shard, and (shard_id,
node) is the direct analog of nano-db's (shard_id, replica_id). A
ReplicaProbe here is therefore addressed by (shard_id, node index), talking
to that node's internal gRPC port.

Every call returns a status rather than raising, for the same reason as
probe.py: a replica being mid-restart under chaos is an expected, honest
data point, not an error.
"""

from __future__ import annotations

import glob
import os
import sys
import tempfile

import numpy as np

_STUB_DIR: str | None = None
_pi = None          # points_internal_service_pb2
_pi_grpc = None      # points_internal_service_pb2_grpc
_points = None       # points_pb2
_common = None       # qdrant_common_pb2
_grpc = None

_PROTO_FILES = [
    "json_with_int.proto",
    "qdrant_common.proto",
    "collections.proto",
    "points.proto",
    "points_internal_service.proto",
]


def ensure_stubs(proto_dir: str) -> None:
    """Generate and import gRPC stubs for the vendored internal protos.

    Generated into a temp dir, same rationale as probe.py: keep build
    artifacts out of the checked-in source tree.
    """
    global _STUB_DIR, _pi, _pi_grpc, _points, _common, _grpc

    if _pi is not None:
        return

    try:
        import grpc                                  # noqa: F401
        from grpc_tools import protoc
    except ImportError as e:
        raise RuntimeError(
            "grpcio and grpcio-tools are required for the Qdrant replica probe.\n"
            "  pip install grpcio grpcio-tools\n"
            f"(import failed: {e})"
        ) from e

    import grpc_tools
    well_known = os.path.join(os.path.dirname(grpc_tools.__file__), "_proto")

    _STUB_DIR = tempfile.mkdtemp(prefix="qdrant_stubs_")
    proto_paths = [os.path.join(proto_dir, f) for f in _PROTO_FILES]
    missing = [p for p in proto_paths if not os.path.exists(p)]
    if missing:
        raise RuntimeError(f"missing vendored proto file(s): {missing}")

    rc = protoc.main([
        "protoc",
        f"--proto_path={proto_dir}",
        f"--proto_path={well_known}",
        f"--python_out={_STUB_DIR}",
        f"--grpc_python_out={_STUB_DIR}",
        *proto_paths,
    ])
    if rc != 0:
        raise RuntimeError(f"protoc failed on {proto_dir} (exit {rc})")

    sys.path.insert(0, _STUB_DIR)
    import grpc as _g
    import points_internal_service_pb2 as _pisvc
    import points_internal_service_pb2_grpc as _pisvc_grpc
    import points_pb2 as _pts
    import qdrant_common_pb2 as _cmn

    _grpc, _pi, _pi_grpc, _points, _common = _g, _pisvc, _pisvc_grpc, _pts, _cmn


def _point_id_to_ext(pid) -> str:
    """PointId oneof -> external id string. num ids stringify as decimal so
    they round-trip through the writer's own `str(int)` ids unchanged."""
    if pid.WhichOneof("point_id_options") == "uuid":
        return pid.uuid
    return str(pid.num)


def _ext_to_point_id(ext_id: str):
    """Inverse of _point_id_to_ext. The writer mints purely numeric ids
    (see qdrant_run_experiment.py), so this only needs the numeric path,
    but falls back to uuid for robustness against a non-numeric id."""
    if ext_id.isdigit():
        return _common.PointId(num=int(ext_id))
    return _common.PointId(uuid=ext_id)


class ReplicaProbe:
    """A direct client for one (shard_id, node) pair."""

    def __init__(self, name: str, shard_id: int, replica_id: int,
                 collection: str, host: str, port: int, timeout_s: float = 2.0):
        self.name = name
        self.shard_id = shard_id
        self.replica_id = replica_id
        self.collection = collection
        self.addr = f"{host}:{port}"
        self.timeout_s = timeout_s
        self._chan = None
        self._stub = None

    def _stub_or_connect(self):
        if self._stub is None:
            self._chan = _grpc.insecure_channel(self.addr)
            self._stub = _pi_grpc.PointsInternalStub(self._chan)
        return self._stub

    def _reset(self) -> None:
        """Drop the channel so the next call redials -- necessary because
        chaos kills and restarts the container on the same host port."""
        try:
            if self._chan is not None:
                self._chan.close()
        except Exception:
            pass
        self._chan = None
        self._stub = None

    # -- RPCs -----------------------------------------------------------

    def list_local_ids(self, page_size: int = 10_000, max_pages: int = 10_000
                        ) -> tuple[bool, set[str]]:
        """Enumerate every live id this node's copy of this shard holds, via
        the shard_id-scoped Scroll RPC, paginated by PointId offset."""
        try:
            stub = self._stub_or_connect()
            out: set[str] = set()
            offset = None
            for _ in range(max_pages):
                scroll = _points.ScrollPoints(
                    collection_name=self.collection,
                    limit=page_size,
                    with_payload=_points.WithPayloadSelector(enable=False),
                    with_vectors=_points.WithVectorsSelector(enable=False),
                )
                if offset is not None:
                    scroll.offset.CopyFrom(offset)
                req = _pi.ScrollPointsInternal(
                    scroll_points=scroll, shard_id=self.shard_id)
                resp = stub.Scroll(req, timeout=self.timeout_s)
                out.update(_point_id_to_ext(p.id) for p in resp.result)
                if not resp.HasField("next_page_offset"):
                    break
                offset = resp.next_page_offset
            return True, out
        except Exception:
            self._reset()
            return False, set()

    def search_batch(self, queries: np.ndarray, k: int) -> tuple[bool, list[list[str]]]:
        """Run the whole query set against this replica in ONE round trip --
        CoreSearchBatch takes a `repeated CoreSearchPoints`, so unlike
        ShardService (no batch RPC), this does not need a per-query loop."""
        try:
            stub = self._stub_or_connect()
            search_points = [
                _pi.CoreSearchPoints(
                    collection_name=self.collection,
                    query=_pi.QueryEnum(nearest_neighbors=_points.Vector(
                        dense=_points.DenseVector(data=[float(x) for x in q]))),
                    limit=k,
                    with_payload=_points.WithPayloadSelector(enable=False),
                )
                for q in queries
            ]
            req = _pi.CoreSearchBatchPointsInternal(
                collection_name=self.collection,
                search_points=search_points,
                shard_id=self.shard_id,
            )
            resp = stub.CoreSearchBatch(req, timeout=self.timeout_s)
            out = [[_point_id_to_ext(sp.id) for sp in batch.result]
                   for batch in resp.result]
            return True, out
        except Exception:
            self._reset()
            return False, []

    def close(self) -> None:
        self._reset()


def build_probes(num_shards: int, replicas_per_shard: int, collection: str,
                 port_fn, host: str = "127.0.0.1",
                 timeout_s: float = 2.0) -> list[ReplicaProbe]:
    """One probe per (shard, node). `port_fn(shard_id, replica_id) -> port`
    so this composes with qdrant_topology.py's port layout the same way
    probe.build_probes composes with chaos_harness.py's."""
    probes = []
    for s in range(num_shards):
        for r in range(replicas_per_shard):
            probes.append(ReplicaProbe(
                name=f"shard-{s}-{r}", shard_id=s, replica_id=r,
                collection=collection, host=host, port=port_fn(s, r),
                timeout_s=timeout_s))
    return probes
