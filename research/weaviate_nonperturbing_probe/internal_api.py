"""
Weaviate's cluster-internal API, as a per-replica probe (issue #43).

Found by enumeration against a live 1.29.0 cluster, not from documentation
(there is none for this API). It listens on CLUSTER_DATA_BIND_PORT -- NOT on
the main HTTP port, where every `/indices/...` and `/replicas/...` path 404s --
and identifies itself as "Weaviate's cluster-internal API for cross-node
communication".

What answers, on `/indices/{class}/shards/{shard}`:

    GET  /status                     -> "READY"                     (text)
    GET  /objects?ids=<b64>          -> binary storobj list         (see below)
    GET  /objects/_digest            -> wants a JSON body
    POST /objects/_search            -> 415 for every Content-Type tried

`ids` is **base64 of a JSON array of UUID strings**; the response is
Weaviate's internal binary object encoding, not JSON, so it is read as bytes
and only its *size* and success are interpreted here. That is enough for what
this project needs from it: presence of known ids on one named replica --
which is exactly the shape of `completeness` (of the ids the writer
confirmed, how many does this replica hold), and exactly what
`qdrant_probe.ListLocalIds` provides on the Qdrant leg.

The point of all of it: this reads ONE replica while its peers stay up, so it
does not perturb the cluster the way #41's isolation probe does.
"""
from __future__ import annotations

import base64
import json
import sys
import urllib.error
import urllib.request

sys.path.insert(0, __file__.rsplit("weaviate_nonperturbing_probe", 1)[0] + "weaviate_probe")

import weaviate_topology as t  # noqa: E402


def shard_name(node: int = 0) -> str | None:
    st, nodes = t.nodes_status(node, verbose=True)
    for n in (nodes.get("nodes") or []):
        for s in (n.get("shards") or []):
            return s.get("name")
    return None


def _base(shard: str) -> str:
    return f"/indices/{t.CLASS_NAME}/shards/{shard}"


def raw_get(node: int, path: str, timeout: float = 10.0) -> tuple[int, bytes]:
    """Binary-safe GET against a node's internal port.

    `weaviate_topology.http_request` decodes as UTF-8 and this API answers in
    a binary encoding, so it must not be used here -- decoding the object
    list as text is how this endpoint first looked like a failure when it had
    actually succeeded.
    """
    url = f"http://127.0.0.1:{t.internal_port(node)}{path}"
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except Exception as e:  # noqa: BLE001 -- a stopped node is a result, not a crash
        return 0, repr(e).encode()


def encode_ids(ids) -> str:
    """The `ids` url param: base64 of a JSON array of UUID strings."""
    return base64.b64encode(json.dumps(list(ids)).encode()).decode()


def objects_present(node: int, shard: str, ids) -> tuple[bool, int]:
    """Ask ONE replica for a specific set of ids, peers untouched.

    Returns (ok, response_size_bytes). The body is Weaviate's internal binary
    encoding; this deliberately does not pretend to parse it. Size is
    monotone in how many of the requested objects the replica actually
    returned, which is what makes a presence comparison possible -- see
    SPEC.md for why that is enough here and where it is not.
    """
    from urllib.parse import quote
    st, body = raw_get(node, _base(shard) + "/objects?ids=" + quote(encode_ids(ids)))
    return st == 200, len(body)


def shard_status(node: int, shard: str) -> tuple[int, str]:
    st, body = raw_get(node, _base(shard) + "/status")
    return st, body.decode(errors="replace").strip()
