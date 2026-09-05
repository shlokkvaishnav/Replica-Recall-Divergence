#!/usr/bin/env python3
"""
Per-id presence on one Weaviate replica (issue #46).

#43's probe answered "how many bytes came back", which is enough for a
feasibility verdict and not enough for `completeness`, a metric defined over
specific ids. This returns the SET of requested ids the replica actually
holds.

How, and why this is not a full decoder: the internal API's response is
Weaviate's binary object encoding, and each object carries its id as **16
bytes big-endian** near the start of its record (observed at offset 18 of a
single-object response on 1.29.0, followed by timestamps, the vector, the
length-prefixed class name, and the properties JSON). Presence is therefore
decided by scanning the response for each requested id's 16-byte form. That
is a much smaller claim than parsing the format, and it is falsifiable: ask
for ids that are absent and the scan must not find them.

The failure mode this must not have is a decoder that always succeeds -- if
the server echoed requested ids anywhere in the body, every id would look
present. `validate()` below is built around exactly that: it requires a mix
of present, never-written, and peer-only ids, and requires the returned set
to equal the constructed expectation, not merely to look plausible.

Usage:
    python research/weaviate_probe_per_id/per_id.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
import uuid
from urllib.parse import quote

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "research", "weaviate_probe"))
sys.path.insert(0, os.path.join(ROOT, "research", "weaviate_nonperturbing_probe"))

import weaviate_topology as t          # noqa: E402
import internal_api as ia              # noqa: E402


def objects_present_ids(node: int, shard: str, ids) -> tuple[bool, set[str]]:
    """Which of `ids` this replica holds. Peers are not touched.

    Returns (ok, present_ids). `ok` is False when the node did not serve the
    request at all -- distinct from "served, and holds none of them", which
    is (True, set()).
    """
    ids = list(ids)
    path = (f"/indices/{t.CLASS_NAME}/shards/{shard}/objects?ids="
            + quote(ia.encode_ids(ids)))
    st, body = ia.raw_get(node, path)
    if st != 200:
        return False, set()
    present = {i for i in ids if uuid.UUID(i).bytes in body}
    return True, present


def _write(objs, consistency="ALL"):
    st, b = t.http_request(t.http_port(0), "POST",
                           f"/v1/batch/objects?consistency_level={consistency}",
                           {"objects": objs}, timeout=120)
    errs = sum(1 for o in b if (o.get("result") or {}).get("errors")) if isinstance(b, list) else -1
    return st, errs


def _mkobjs(ids, seed):
    rng = np.random.default_rng(seed)
    return [{"class": t.CLASS_NAME, "id": i, "properties": {"vid": i[-6:]},
             "vector": [float(x) for x in rng.standard_normal(t.VECTOR_DIM)]}
            for i in ids]


def validate() -> int:
    """SPEC step 4: the returned set must equal a CONSTRUCTED expectation
    containing present, absent, and peer-only ids -- the only test that
    distinguishes a working decoder from one that returns whatever it was
    asked for."""
    checks = []

    def check(name, cond, detail=""):
        checks.append(bool(cond))
        print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  -- {detail}" if detail else ""))

    shard = ia.shard_name(0)
    print(f"shard: {shard}")

    PRESENT = [str(uuid.UUID(int=i)) for i in range(20)]        # written to all
    ABSENT = [str(uuid.UUID(int=900000 + i)) for i in range(10)]  # never written
    PEERONLY = [str(uuid.UUID(int=910000 + i)) for i in range(10)]  # written while node2 down

    print("\nsetup: write 20 objects at consistency ALL")
    st, errs = _write(_mkobjs(PRESENT, 1))
    print(f"  write: {st}, errors={errs}")

    print("\n1. control -- same request to all three replicas, healthy cluster")
    sets = []
    for n in range(3):
        ok, got = objects_present_ids(n, shard, PRESENT + ABSENT)
        sets.append(got)
        print(f"   node{n}: ok={ok} present={len(got)}/{len(PRESENT)} absent_found={len(got & set(ABSENT))}")
    check("all three replicas agree", len({frozenset(s) for s in sets}) == 1)
    check("every written id is found", sets[0] >= set(PRESENT),
          f"missing {len(set(PRESENT) - sets[0])}")
    check("no never-written id is found (the always-succeeds trap)",
          not (sets[0] & set(ABSENT)), f"false positives: {sorted(sets[0] & set(ABSENT))[:3]}")

    print("\n2. peer-only ids -- stop node2, write, keep node2 down, probe all three")
    subprocess.run(["docker", "stop", t.container_name(2)], capture_output=True)
    time.sleep(5)
    st, errs = _write(_mkobjs(PEERONLY, 2), consistency="ONE")
    print(f"   write while node2 down: {st}, errors={errs}")
    ok0, got0 = objects_present_ids(0, shard, PEERONLY)
    ok2, got2 = objects_present_ids(2, shard, PEERONLY)
    print(f"   node0 (up):   ok={ok0} present={len(got0)}/{len(PEERONLY)}")
    print(f"   node2 (down): ok={ok2} present={len(got2)}")
    check("a peer that received the writes reports them", ok0 and got0 == set(PEERONLY),
          f"got {len(got0)}")
    check("a stopped node does not serve (ok=False), rather than answering", not ok2)

    print("\n3. the constructed mix on the diverged replica, ALL PEERS UP")
    subprocess.run(["docker", "start", t.container_name(2)], capture_output=True)
    deadline = time.time() + 120
    first = None
    while time.time() < deadline:
        ok, got = objects_present_ids(2, shard, PEERONLY)
        if ok:
            first = got
            break
        time.sleep(0.2)
    if first is None:
        check("node2 answered after restart", False, "never answered")
    else:
        # #46 pre-registered SET EQUALITY against a constructed expectation.
        # The complication -- and the reason an earlier version of this file
        # weakened the assertion to a superset check, which PR #47's review
        # caught -- is that the peer-only ids converge at an unpredictable
        # moment, so "the expectation" is only defined either side of that,
        # not across it. It is defined at two moments, and equality is
        # asserted at both:
        #   before convergence: the mix is exactly the always-written ids
        #   after  convergence: exactly always-written + peer-only
        # Never absent ids, at either moment.
        print(f"   node2's first answer after restart: {len(first)}/{len(PEERONLY)} peer-only ids")
        ok_mix, mix = objects_present_ids(2, shard, PRESENT + ABSENT + PEERONLY)
        converged = len(mix & set(PEERONLY)) == len(PEERONLY)
        expected = set(PRESENT) | (set(PEERONLY) if converged else set())
        print(f"   mixed request: present={len(mix & set(PRESENT))}/{len(PRESENT)} "
              f"absent={len(mix & set(ABSENT))} peer_only={len(mix & set(PEERONLY))}/{len(PEERONLY)} "
              f"({'post' if converged else 'pre'}-convergence)")
        check("the mix EQUALS the constructed expectation (#46's decision metric)",
              mix == expected,
              f"unexpected {sorted(mix - expected)[:3]}, missing {sorted(expected - mix)[:3]}")
        if not converged:
            check("pre-convergence: no peer-only id is reported", not (mix & set(PEERONLY)))
        check("the mix finds no never-written id", not (mix & set(ABSENT)))

    print("\n4. cross-check against #43's size-based probe")
    okA, sizeA = ia.objects_present(0, shard, PRESENT)
    okB, sizeB = ia.objects_present(0, shard, ABSENT)
    _, gotA = objects_present_ids(0, shard, PRESENT)
    _, gotB = objects_present_ids(0, shard, ABSENT)
    print(f"   present ids: size={sizeA} decoded={len(gotA)}   absent ids: size={sizeB} decoded={len(gotB)}")
    check("size and decoded count agree on direction",
          (sizeA > 0) == (len(gotA) > 0) and (sizeB == 0) == (len(gotB) == 0))

    n_fail = sum(1 for c in checks if not c)
    print(f"\n{len(checks) - n_fail}/{len(checks)} checks passed")
    print("\nNot covered: that the 16-byte id offset is stable across Weaviate "
          "versions (the image is digest-pinned for exactly this reason), and "
          "that the scan cannot false-positive on an id that appears inside "
          "another object's vector bytes -- improbable at 16 bytes, unproven.")
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(validate())
