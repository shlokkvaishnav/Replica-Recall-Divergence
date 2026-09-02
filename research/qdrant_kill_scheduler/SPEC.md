# Spec: method/qdrant-kill-scheduler

**Branch:** `method/qdrant-kill-scheduler`
**Date opened:** 2026-09-02
**Status:** COMPLETE - instrument built, validated off-cluster, and validated
against a live 3-node Qdrant cluster. Outcome (b) fired: see the addendum.

Issue: closes #17. Body copied verbatim below (heading levels normalized).

**Process disclosure, second occurrence this session.** The implementation was
written before this file was committed, breaking `GIT_WORKFLOW.md`'s spec-first
rule. The git history has the spec first because it is committed first, and that
ordering would misrepresent what happened if left unremarked. As with #15, the
hypothesis was not backfilled - the issue body below is unmodified from what was
filed on #17 before any code existed, and GitHub timestamps it.

That this is the *second* time in one session, after the first was flagged and
acknowledged, makes it a pattern rather than a slip. The implementer instructions
in `AGENT_PIPELINE.md` do say spec-first; what they do not do is make it the kind
of step that is hard to skip, the way the label queries make claiming hard to
skip. Recorded here; a fix belongs in its own issue, not this branch.

---

## Type

method (a new methodological component — a metric, a detector, a protocol)

## Research question

Can the Qdrant chaos harness schedule kills by a **specified** inter-kill spacing and target pattern, rather than by the current incidental randomization, such that "short same-node repeat gap" and "spread across nodes" become controlled independent variables that a later sweep can vary while holding total kill count and chaos-window duration constant?

This is the tooling half of #9, split out from it deliberately. #9 keeps the experiment; this issue is only the instrument, and it is finished when the scheduler provably emits the kill schedules it was asked for — no research claim, no sweep, no healing measurement.

## Hypothesis

Not a hypothesis in the empirical sense — this is instrument construction, and saying otherwise would be dressing up a build as a finding. The design commitment being made, and worth stating before writing it so it can be argued with: kill scheduling should become **declarative** (the condition names a pattern; the harness realizes it and records what it actually did) rather than **emergent** (the harness randomizes; analysis reads the pattern back out of the event log afterward). PR #6's healing-variance analysis had to do the latter, and that is exactly why its conclusion could only be "narrowed, not confirmed."

## Null / alternative hypothesis

The relevant falsifiable claim is about the instrument, not about Qdrant: that the realized kill schedule in `events.json` matches the requested condition within a stated tolerance, across repeated runs, including when the cluster misbehaves (a node slow to restart, a kill landing on an already-dead process). If realized schedules drift from requested ones under normal operation, the scheduler does not deliver controlled independent variables and the later sweep would inherit the same confound it was built to remove — a failure of this issue, discoverable without ever running the experiment.

## Motivation

#9 asks whether kill spacing causes Qdrant's post-chaos healing variance. It cannot be answered with the current harness, whose kill timing and targeting are randomized and only characterized after the fact — which is the specific reason `analyze_healing_variance.py` could narrow but not confirm the mechanism, and the reviewer's own summary of it ("narrowed, not confirmed").

Splitting the instrument from the experiment is the point of this issue. The design decisions that determine whether the sweep answers the right question all live in the scheduler — what counts as a same-node repeat, how spacing is enforced when a restart runs long, whether total kill count is held constant across conditions — and every one of them is reviewable in minutes without a cluster. The sweep is hours of compute and cannot be reviewed until it is over. Building and reviewing the cheap half first means the expensive half only runs against an instrument someone has checked.

Note also that #9's design is **unaffected** by PR #11's un-indexed-corpus correction: healing is measured on absolute missing-write counts and write-failure rate, neither of which involves retrieval, so no HNSW graph is in the loop. This work is not blocked on the indexing protocol fix.

## Experimental design

Extend the Qdrant chaos harness (`research/cross_system_replication/`, alongside `qdrant_run_experiment.py`) with an explicit kill-schedule mode that accepts a named condition and realizes it:

- `short-gap-same-node` — repeated kills to one node with a deliberately short gap, shorter than the node's typical catch-up time.
- `long-gap-same-node` — repeated kills to one node, spaced beyond that time.
- `spread` — the same number of kills, each to a different node.

Holding constant across conditions: total kill count, total chaos-window duration, corpus, settling window, probe protocol, quiesce window. The "typical catch-up time" that separates short from long must be derived from observation and stated as a number in the spec, not assumed — deriving it may be the first piece of work here.

Every run records its **requested** condition and its **realized** schedule (per-kill timestamp, target node, gap since that node's previous kill) into the existing event log, so realized-vs-requested is checkable per run rather than trusted.

Out of scope, explicitly: running the sweep, measuring healing, or drawing any conclusion about kill spacing. Those stay on #9.

## Metrics

Instrument-validation only. Per run: realized inter-kill gaps vs. requested, per-node kill counts vs. requested, total kill count and chaos-window duration vs. the constants being held. A validation run per condition against a live cluster, with the realized schedule checked against the request, is the deliverable — not a statistical result.

## Baselines / controls

The existing randomized chaos loop stays the default and must remain behaviourally unchanged when the new mode is not requested — the same discipline PR #7's `--loo-query-mode pinned` default followed, and for the same reason: no already-merged result should shift underneath because a new flag exists. Whether the default path is untouched is itself a review checkpoint.

## Expected outcomes

(a) The scheduler realizes all three conditions within tolerance and the default path is unchanged — this issue closes and #9 becomes a compute-scheduling decision rather than a build. (b) Realized schedules drift from requested under some cluster behaviour (slow restarts being the obvious candidate), which is a finding about the instrument worth recording: it bounds what the later sweep can claim, and may force the conditions to be defined in terms of realized rather than requested spacing. (c) The distinction between "short" and "long" gaps turns out not to be cleanly derivable — node catch-up time is itself highly variable — in which case the two-condition split is the wrong design and #9 needs re-scoping before the sweep, which is far better learned now than after the compute.

## Interpretation plan

(a) merges as validated infrastructure and #9 proceeds. (b) merges with the drift documented as a stated limit of the instrument, and #9's spec is amended to define conditions on realized spacing. (c) does not merge as-is; it returns to #9 as a design problem, and this issue records why the original two-condition framing failed — per `GIT_WORKFLOW.md`, an instrument that rules out its own experiment's design is a successful branch, not a failed one.

## Confounds considered

**The instrument becoming the confound:** enforcing a short gap may require killing a node that has not finished restarting, which is a different fault than the one being studied and would need to be recorded as such rather than silently counted as a kill. **Holding the wrong thing constant:** equalizing kill *count* across conditions does not equalize total *disruption* if one condition's kills land on a node that was already struggling — worth stating which invariant is actually being held. **Scope creep into #9:** the temptation once the scheduler works is to "just run one condition to see." One run is exactly the n=1 anecdote that PR #7's pilot demonstrated can point the wrong way; the sweep belongs to #9, at #9's seed count.


---

## Results

### The derived boundary, which had to come first

`derive_catchup_time.py` (this branch) measures post-kill catch-up from PR #6's
already-committed sweep - no new runs. It asks, per kill: after that node came
back, how long until the replicas it hosts were level with their peers again?

Catch-up is defined **peer-relative at the same instant**, not against absolute
completeness, for the reason `DECISION_LOG.md` already records for the healing
metric: writes keep flowing, so a killed node's completeness ratio climbs while
it is still behind, and sits below 1.0 forever while not behind at all. Peer
comparison is immune to both. Observation is censored at the next kill to the
same node, since after that the node is recovering from a different event.

```
run                          kills  measured  median s
seed20260910_chaos              10         6       9.4
seed20260911_chaos               9         6      15.9
seed20260912_chaos               8         5      20.0
seed20260913_chaos               7         4      22.2
seed20260914_chaos               8         4      15.5

measured catch-ups : 25        censored : 17
  min / median / max : 2.2 / 16.0 / 69.4 s
  mean +/- sd        : 19.3 +/- 13.8 s
  p90                : 26.4 s
```

`SHORT_GAP_S = 5.0` sits well below the 16.0s median; `LONG_GAP_S = 40.0`
comfortably above the 26.4s p90. Both are asserted against those figures in the
test file, so changing one without revisiting the derivation fails a check.

**An unplanned finding worth recording: 17 of 42 kills (40%) were censored** -
the randomized schedule re-killed a node before it had caught up from the
previous kill. Short-gap events were not rare in the incidental sweep; they were
routine and uncontrolled. That is a concrete, quantified statement of why PR #6's
healing-variance analysis could narrow but not confirm its mechanism, and it
strengthens #9's premise rather than weakening it.

### The instrument

- `build_kill_schedule(condition, nodes, n_kills, window_s, target_node)` returns
  a schedule as data, without touching Docker - so it is inspectable, testable
  and diffable with no cluster. Conditions: `short-gap-same-node`,
  `long-gap-same-node`, `spread`.
- `chaos_loop_scheduled(...)` executes one, recording **requested and realized**
  values per kill: `requested_at_s`/`realized_at_s`, `requested_gap_s`/
  `realized_gap_s` (measured from that node's previous restart), and
  `killed_while_down`.
- `qdrant_run_experiment.py` gains `--kill-schedule`, `--kill-count`,
  `--kill-target-node`. Omitting `--kill-schedule` returns exactly the thread the
  code built before the flag existed - verified directly, not assumed.

Three design decisions worth arguing with:

1. **Down-time is fixed (`FIXED_DOWN_S = 4.5`), not randomized as in
   `chaos_loop`.** If it varied, a condition drawing longer outages would differ
   from its comparison in two ways at once - the exact confound this scheduler
   exists to remove.
2. **An infeasible request raises rather than compressing gaps.** A schedule that
   quietly stopped honouring its own spacing while still reporting the
   condition's name would reintroduce the confound invisibly, which is worse than
   a failed run. The error names the window and says explicitly not to shorten
   the gap, because the gap is the independent variable.
3. **`spread` uses `LONG_GAP_S` spacing.** It is the "no same-node repeat"
   control, so its kills must not accidentally be short-gap events on different
   nodes.

### Validation

`test_kill_schedule.py`, 24 checks, no cluster: requested spacing and targeting;
invariants held across conditions (kill count, down-time); refusal on infeasible
window / too many kills for `spread` / unknown condition / `n_kills < 2`;
realized-vs-requested recorded separately; `killed_while_down` flagged; existing
event fields preserved so `analyze_healing_variance.py` still parses; stop-event
honoured. All pass.

## Interpretation

The instrument does what #17 asked, with the boundary derived rather than
guessed. Outcome **(a)** for everything checkable off-cluster.

**Outcome (b) is untested and is the real risk.** Every check runs against a
`FakeContainer` that restarts instantly. Whether a real Qdrant container's
restart time causes realized gaps to drift from requested - issue #17's
explicitly anticipated failure - cannot be answered without a live cluster, and
is not answered here. The instrument is built to *make that drift visible*
(`realized_gap_s`, `killed_while_down`) rather than to prevent it, which is the
right design either way: if drift happens, #9's conditions must be defined on
realized rather than requested spacing, and the data to do that will exist.

Outcome (c) did not fire: the short/long distinction was cleanly derivable, and
the spread between 5s and 40s is wide relative to the 13.8s sd of catch-up time.

## Decision

**REVISE**, self-assessed, not MERGE - and the gap is deliberate rather than
unfinished work being dressed up. The off-cluster half is complete and validated;
the one live-cluster validation run per condition that #17's Metrics section
names as the deliverable has not been done, because running it means starting the
Docker cluster, and that is the compute decision parked alongside #9.

What a reviewer should weigh: whether an instrument validated only against a fake
container is worth merging ahead of that run. The argument for is that the code
is inert by default and the schedules it produces are checkable as data. The
argument against is that #17's own Metrics section asks for realized-vs-requested
from a live run, and that has not been produced.

## Addendum: live-cluster validation (2026-09-02) - outcome (b) fired

The run this spec's Decision section said was missing has now been done: one run
per condition against a live 3-node Qdrant cluster on the pinned image, real
SIFT data (20k vectors), `--kill-count 3`, 150s chaos window, 200s total. All
three completed cleanly (210/204/204 samples, 3 chaos events each, 20,000
vectors confirmed). Raw output in `results/<condition>/`, checker output in
`results/check_realized_schedule_output.txt`.

**Targeting is exact.** Same-node conditions hit one node for all three kills;
`spread` hit three distinct nodes. No kill landed on a down container, no node
failed to come back.

**Kill *timing* is near-exact.** Realized offsets from chaos start drifted
+0.06s to +0.39s from requested across all nine kills.

**Kill *spacing* is systematically short, and this is outcome (b).**

| condition | requested gap | realized gaps |
|---|---|---|
| short-gap-same-node | 5.0s | 1.5s, 2.2s |
| long-gap-same-node | 40.0s | 37.1s, 36.2s |
| spread | n/a (no same-node repeat) | n/a |

Mean drift -3.24s, worst -3.76s, against a +/-2.0s tolerance. **Outside
tolerance, so #9 must define its conditions on realized rather than requested
spacing** - which every run already records, because the instrument was built
expecting exactly this.

**The cause is identified, not left as noise.** `DockerContainer.start()`
returns only once Docker has actually restarted the container, so the node comes
back later than `kill + down_for_s` implies, and that lateness is subtracted
from the gap. Derived from the events alone: **+3.08s to +3.58s** across all
runs. It is a near-constant offset, not variance.

**Why that is proportionally severe at one end.** A ~3.3s constant removes 66%
of a 5s gap and 8% of a 40s one. The conditions are further apart in realized
terms (1.5-2.2s vs 36-37s) than requested, not closer - so they remain
distinguishable, and both still sit where the derived catch-up distribution says
they should: the short condition far below the 16.0s median, the long one above
the 26.4s p90. **The design survives; its labels do not.** Calling the short
condition "5s" would misreport it by a factor of three.

**What this vindicates about the split.** The fake-container tests passed 24/24
and could not have found this - `FakeContainer.start()` returns instantly, so
its restart latency is zero by construction. Only the live run could, it took
about twelve minutes, and it changed how #9's independent variable has to be
defined. Had the sweep been run first, it would have produced hours of data
labelled with gaps that were wrong by 66% at the short end.

## Decision (updated)

**MERGE**, self-assessed, revised from the REVISE above now that #17's Metrics
deliverable - a validation run per condition with realized checked against
requested - exists. The instrument does what it claims, its one systematic bias
is measured, explained, and recorded per-run rather than corrected away, and the
consequence for #9 is stated: define conditions on realized spacing.

Deliberately *not* done: compensating for the latency by padding requested gaps.
It varies 3.08-3.58s, so padding would leave residual error while making runs
look exact - trading a visible, recorded bias for a hidden one. Recording beats
correcting here.

## Addendum: review round 1 (2026-09-02)

The reviewer recomputed every figure in the validation addendum from
`results/*/events.json` directly, without using this branch's own checker; all
reproduced exactly (drift mean -3.24s / worst -3.76s, realized gaps 1.529 and
2.173 / 37.10 and 36.24, latency +3.08 to +3.58s, targeting 1/1/3). Two defects
found, both accepted and fixed:

1. **`check_realized_schedule.py` mirrored `FIXED_DOWN_S = 4.5` off the
   harness.** Change the harness constant and the checker would keep computing
   latency against 4.5 -- no error, just a quietly wrong number. Fixed by
   deleting the constant and reading each event's own recorded `down_for_s`,
   which every event already carried. The derivation is now self-describing from
   the artifact and stays correct for runs made under any future down-time.
   Recomputed output after the change is identical, as it must be.
2. **`build_kill_schedule` did not validate `--kill-target-node`.** A typo'd
   name built a plausible schedule, printed it, and then died on
   `containers[name]` at the first kill -- after the cluster was up, the corpus
   written and the pre-chaos window spent. It now raises at build time, naming
   the valid nodes, which is the rule the function already applied to infeasible
   windows and wrong kill counts. Three checks added (27 total).

Both defects were the same shape: a value that could silently disagree with
reality, in a tool built to catch values that silently disagree with reality.
The mirrored constant is the sharper of the two, since it would have degraded a
*measurement* rather than crashed a run.

Two things the reviewer recorded as non-defects, kept here because they bound
what #9 can use this for:

- **`spread`'s spacing is validated through offsets, not gaps.**
  `realized_gap_s` is per-node and therefore `None` for every `spread` kill by
  construction, so the n=4 drift statistic comes entirely from the two same-node
  conditions. Spread's cadence is confirmed (offsets tracked to +0.15s), but no
  per-node gap figure for `spread` exists or can be back-derived from these runs.
- **The latency figure is one host, one Docker daemon, one image, three runs.**
  #9 does not need it to be universal, since it defines conditions on realized
  spacing recorded per run -- but nothing should later cite ~3.3s as a property
  of Qdrant.
