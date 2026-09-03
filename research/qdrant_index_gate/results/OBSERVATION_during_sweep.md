# Read-only observation taken during cell 5 (thr5000_n100k), 2026-09-04

`GET /collections/{name}` on node 0 while the gate was at plateau 0.8550 (85,745 / 100,288):

- `optimizer_config.indexing_threshold: 5000` -- the flag reached Qdrant.
- `optimizer_config.default_segment_number: 0` (auto); `segments_count: 4` per node over 2 shards = 2 segments per shard.
- `hnsw_config.full_scan_threshold: 10000` (KB). [Corrected in review round 1: this governs payload-filtered search planning only, per Qdrant's indexing docs; it says nothing about how an unfiltered search over the tail segment is served.]
- `/cluster`: local shards 52,743 + 47,545 points.

Un-indexed per node ~14.5k vectors ~ 7.3k per shard ~ 3.7 MB: one appendable segment per shard, below the 5 MB threshold, and -- an inference consistent with the documented merge optimizer, not an observation -- not merged because the segment count is already at target. The tail is therefore "everything written since the last merge" -- proportional to the write phase, not a fixed size, and insensitive to `indexing_threshold` until the appendable segment itself exceeds it. Prediction, written before the 1000 KB cells ran: at 1000 KB the 3.7 MB tail indexes in place and the gate closes (smoke run 3 at 1000 KB closed at 0.993).

[Withdrawn in review round 1: an earlier version of this note claimed `full_scan_threshold` made the tail segment exact-scanned regardless of the gate. It does not apply to unfiltered search.]

## Cell 7, thr5000_n200k seed 20260976 -- the first closed gate (recorded 2026-09-04, before the 1000 KB cells ran)

Gate closed at t=208.9s with min fraction 0.9659 after 13.6s. Writers resumed; worst-replica indexed fraction by telemetry sample: 0.9658 (208.9s) -> 0.9379 (214s) -> 0.8982 (219s) -> 0.8575 (240s) -> 0.8417 (266s) -> 0.8045 (327.6s, end). `segments_count` rose 4 -> 6 at 219s -> 7 at 271s. Two small recoveries (+1.5%, at 245s and 271s) are the optimizer indexing a filled segment; the write rate (~1.6k vectors/s over 4 writers) outruns it. `base_frac_0p95` = 0.042 (1 of 24 samples). Its replicate, seed 20260977, plateaued at 0.9304 and never closed -- the tail at gate open varies run to run, presumably with where the last merge fell relative to the pause.

`probe_s` median 3.61s at 200k -- PR #25's sampling floor is present here too (outcome (d)). `index_recall` median 0.99 over a corpus that was 80-97% indexed.

Reading, ahead of the remaining cells: the gate can be closed, but it does not stay closed at the protocol's write rate. A front-loaded corpus is a property of the first ~10s of the baseline, after which the appendable tail grows monotonically (minus optimizer catch-ups). That is null hypothesis (ii), and it is independent of the threshold value unless the threshold is small enough for the optimizer to index each new segment as fast as writers fill one.
