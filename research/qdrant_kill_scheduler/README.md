# qdrant_kill_scheduler

The instrument for [#9](https://github.com/shlokkvaishnav/Replica-Recall-Divergence/issues/9),
split out from it deliberately: this directory makes kill spacing and kill
targeting **controlled independent variables**. It does not run the experiment
and draws no conclusion about healing.

Read [`SPEC.md`](SPEC.md) first.

## Why this exists

PR #6's Qdrant sweep found post-chaos healing varying wildly across seeds (84%,
0%, 25%, -32%, 100% recovery). A follow-up analysis correlated the worst outcome
with the shortest same-node repeated-kill gap — but could only narrow the
mechanism, never confirm it, because kill timing was *randomized and read back
out of the event log afterwards* rather than set.

A correlation found in a variable you did not control is a hypothesis. The same
comparison run against a variable you did set is a test. This directory is what
turns the first into the second.

## The boundary between "short" and "long" is measured, not chosen

```
python research/qdrant_kill_scheduler/derive_catchup_time.py
```

Reads PR #6's committed sweep (`../cross_system_replication/results_sweep/`) —
no new runs, no cluster — and measures how long a killed node takes to become
level with its peers again, peer-relative at each instant rather than against
absolute completeness (writes keep flowing; see `DECISION_LOG.md`'s dilution-trap
entry for why the ratio misleads here).

```
measured catch-ups : 25        censored : 17
  min / median / max : 2.2 / 16.0 / 69.4 s
  mean +/- sd        : 19.3 +/- 13.8 s
  p90                : 26.4 s
```

`SHORT_GAP_S = 5.0` sits below the median, `LONG_GAP_S = 40.0` above the p90.
Both constants are asserted against these numbers in the test file, so moving one
without revisiting the derivation fails a check.

**Worth noticing in that output:** 17 of 42 kills were *censored* — the random
schedule re-killed a node before it had caught up. Short-gap events were not rare
in the existing sweep, they were routine and uncontrolled, which is a quantified
version of exactly why the earlier analysis could not separate them.

## Using it

```
# unchanged behaviour -- randomized chaos, exactly as before this branch
python research/cross_system_replication/qdrant_run_experiment.py ...

# controlled conditions
... --kill-schedule short-gap-same-node --kill-count 3 --chaos-duration 150
... --kill-schedule long-gap-same-node  --kill-count 3 --chaos-duration 150
... --kill-schedule spread              --kill-count 3 --chaos-duration 150
```

Omitting `--kill-schedule` returns exactly the code path that existed before the
flag — the same discipline `--loo-query-mode pinned` follows, so no merged result
shifts because a new option appeared.

Each run records **requested and realized** values per kill (`requested_gap_s` vs
`realized_gap_s`, measured from that node's previous restart), plus
`killed_while_down` for the case a short gap can produce: killing a container
that has not finished coming back, which is a different fault from the one under
study and must be visible rather than counted as an ordinary kill.

An infeasible request raises instead of compressing gaps. A schedule that quietly
stopped honouring its own spacing while still reporting its condition's name
would reintroduce the confound invisibly — worse than a failed run.

## What the live run found

Validated against a live 3-node cluster, one run per condition (`results/`):

| condition | requested gap | realized gaps | targeting |
|---|---|---|---|
| `short-gap-same-node` | 5.0s | **1.5s, 2.2s** | 1 node, as asked |
| `long-gap-same-node` | 40.0s | **37.1s, 36.2s** | 1 node, as asked |
| `spread` | n/a | n/a | 3 distinct nodes, as asked |

Targeting is exact and kill *timing* lands within +0.4s of request. Kill
*spacing* is systematically ~3.3s short, because `docker start` returns only
once the container is actually back -- measured at **+3.08s to +3.58s**, a
near-constant offset rather than noise.

A constant ~3.3s takes 66% off a 5s gap and 8% off a 40s one. The conditions end
up *further* apart in realized terms than requested, and both still sit where the
catch-up distribution says they should. **The design survives; the labels do
not** -- calling the short condition "5s" misreports it threefold, so #9 must
define its conditions on realized spacing. Every run records it.

```
python research/qdrant_kill_scheduler/check_realized_schedule.py
```

The latency is recorded, not compensated for. Padding requested gaps to absorb
it would leave residual error while making runs *look* exact -- trading a
visible bias for a hidden one.

## Validating it

```
python research/qdrant_kill_scheduler/test_kill_schedule.py     # 24 checks, no cluster
```

The tests print what they do **not** cover rather than leaving it implied. The
important gap is the one above: every check runs against a fake container that
restarts instantly, so its restart latency is zero by construction. All 24 passed
while the real ~3.3s bias sat undetected — which is precisely why the live run
was worth twelve minutes, and why the instrument was built to make drift visible
rather than to prevent it.
