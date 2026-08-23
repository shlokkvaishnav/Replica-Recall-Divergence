# Vendored Qdrant internal gRPC protos

These five `.proto` files are copied verbatim from `qdrant/qdrant`'s
`lib/api/src/grpc/proto/` on GitHub (pulled 2026-08-23, `master` at the time,
verified against the `qdrant/qdrant:latest` Docker image current on that
date). They are **not** shipped by the `qdrant-client` PyPI package -- that
package only bundles the public client-facing protos (external API on
6333/6334). `points_internal_service.proto` in particular defines the
`PointsInternal` gRPC service that Qdrant exposes on its cluster-internal
port (6335 by default, shared with Raft consensus transport) with no
authentication and no `ServerReflection` support -- see the addendum dated
2026-08-23 in `../SPEC.md` for how this was found and confirmed.

`points.proto`, `collections.proto`, `qdrant_common.proto`, and
`json_with_int.proto` are included only because `points_internal_service.proto`
imports them (message types like `Vector`, `Filter`, `ReadConsistency`).

This is not a documented or supported integration point. Qdrant could
change, rename, or remove this surface in any future release without notice.
`qdrant_probe.py` records the exact image tag/digest a run was executed
against for that reason -- treat any result from this branch as tied to that
specific version, not to "Qdrant" in general.
