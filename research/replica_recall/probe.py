"""
gRPC probe: talks to individual shard replicas, bypassing the coordinator.

This is what makes the experiment possible. ShardService already exposes
everything needed to interrogate a single replica in isolation:

  Search(vector, k)  -> what THIS replica would answer, no scatter-gather,
                        no quorum, no epoch check on the read path
  ListLocalIds()     -> exactly the live (non-tombstoned) ids this replica
                        holds; shard_service_impl.hpp already filters
                        tombstoned nodes out of this response
  Stats()            -> element_count / shard_id

Going through the coordinator would merge replicas and hide precisely the
divergence under study, so every call here is direct to the replica port.

Replicas are being killed by the chaos loop while this runs, so every call
returns a status rather than raising: an unreachable replica is a data point
(and, notably, the *honest* failure mode -- the one you can actually see),
not an error.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile

import numpy as np

_STUB_DIR: str | None = None
_pb2 = None
_pb2_grpc = None
_grpc = None


def ensure_stubs(proto_path: str) -> None:
    """Generate and import the gRPC stubs for nanodb_cluster.proto.

    Generated into a temp dir rather than the repo so the experiment leaves
    no build artifacts behind in a source tree that is checked in.
    """
    global _STUB_DIR, _pb2, _pb2_grpc, _grpc

    if _pb2 is not None:
        return

    try:
        import grpc                                  # noqa: F401
        from grpc_tools import protoc
    except ImportError as e:
        raise RuntimeError(
            "grpcio and grpcio-tools are required for the replica probe.\n"
            "  pip install grpcio grpcio-tools\n"
            f"(import failed: {e})"
        ) from e

    _STUB_DIR = tempfile.mkdtemp(prefix="nanodb_stubs_")
    proto_dir = os.path.dirname(os.path.abspath(proto_path))
    proto_name = os.path.basename(proto_path)

    rc = protoc.main([
        "protoc",
        f"--proto_path={proto_dir}",
        f"--python_out={_STUB_DIR}",
        f"--grpc_python_out={_STUB_DIR}",
        proto_name,
    ])
    if rc != 0:
        raise RuntimeError(f"protoc failed on {proto_path} (exit {rc})")

    # The generated _pb2_grpc module does a bare `import nanodb_cluster_pb2`,
    # so its directory has to be importable.
    sys.path.insert(0, _STUB_DIR)
    import grpc as _g
    import nanodb_cluster_pb2 as _p
    import nanodb_cluster_pb2_grpc as _pg

    _grpc, _pb2, _pb2_grpc = _g, _p, _pg


class ReplicaProbe:
    """A direct client for one shard replica."""

    def __init__(self, name: str, shard_id: int, replica_id: int,
                 host: str, port: int, timeout_s: float = 2.0):
        self.name = name
        self.shard_id = shard_id
        self.replica_id = replica_id
        self.addr = f"{host}:{port}"
        self.timeout_s = timeout_s
        self._chan = None
        self._stub = None

    def _stub_or_connect(self):
        if self._stub is None:
            self._chan = _grpc.insecure_channel(self.addr)
            self._stub = _pb2_grpc.ShardServiceStub(self._chan)
        return self._stub

    def _reset(self) -> None:
        """Drop the channel so the next call redials.

        Necessary because the chaos loop kills and restarts the process on
        the same port; a channel held across that will keep failing.
        """
        try:
            if self._chan is not None:
                self._chan.close()
        except Exception:
            pass
        self._chan = None
        self._stub = None

    # -- RPCs ---------------------------------------------------------------

    def list_local_ids(self) -> tuple[bool, set[str]]:
        try:
            resp = self._stub_or_connect().ListLocalIds(
                _pb2.ListLocalIdsRequest(), timeout=self.timeout_s)
            if not resp.ok:
                return False, set()
            return True, set(resp.external_ids)
        except Exception:
            self._reset()
            return False, set()

    def search(self, query: np.ndarray, k: int) -> tuple[bool, list[str]]:
        try:
            req = _pb2.SearchRequest(vector=query.tolist(), k=k)
            resp = self._stub_or_connect().Search(req, timeout=self.timeout_s)
            if not resp.ok:
                return False, []
            return True, [r.external_id for r in resp.results]
        except Exception:
            self._reset()
            return False, []

    def search_batch(self, queries: np.ndarray, k: int) -> tuple[bool, list[list[str]]]:
        """Run the whole query set against this replica.

        ShardService has no batch Search RPC, so this is a loop. If any single
        query fails the replica is treated as unreachable for this sample:
        a partial sample would mix pre- and post-failure state within one
        measurement and is worse than no sample.
        """
        out: list[list[str]] = []
        for q in queries:
            ok, res = self.search(q, k)
            if not ok:
                return False, []
            out.append(res)
        return True, out

    def alive(self) -> bool:
        try:
            resp = self._stub_or_connect().Ping(_pb2.PingRequest(),
                                                 timeout=self.timeout_s)
            return bool(resp.ok)
        except Exception:
            self._reset()
            return False

    def close(self) -> None:
        self._reset()


def build_probes(num_shards: int, replicas_per_shard: int,
                 port_fn, host: str = "127.0.0.1",
                 timeout_s: float = 2.0) -> list[ReplicaProbe]:
    """One probe per replica. `port_fn(shard_id, replica_id) -> port` so this
    works against both chaos_harness.py's port layout and deploy configs."""
    probes = []
    for s in range(num_shards):
        for r in range(replicas_per_shard):
            probes.append(ReplicaProbe(
                name=f"shard-{s}-{r}", shard_id=s, replica_id=r,
                host=host, port=port_fn(s, r), timeout_s=timeout_s))
    return probes
