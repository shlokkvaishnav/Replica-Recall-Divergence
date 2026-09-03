<!--
Copy this file into the branch that answers it (e.g. as the first commit on
`research/<topic>`, named `SPEC.md` at the root of whatever directory the
work lives in) and fill it in BEFORE writing the implementation or looking
at any results. See ../GIT_WORKFLOW.md for when a branch needs this versus
a one-line experiment note.

If the work becomes exploratory partway through — an unexpected observation
redirects it — do not rewrite this file to look like it predicted the
detour. Add a dated addendum at the bottom instead.
-->

# Spec: <branch name>

**Branch:** `research/<topic>` or `experiment/<name>`
**Date opened:** YYYY-MM-DD
**Status:** DRAFT / IN PROGRESS / COMPLETE

## Research question

What are we trying to determine? One or two sentences, specific enough to be answerable.

## Hypothesis

What do we currently expect, and why?

## Null / alternative hypothesis

What result would contradict the hypothesis above? State it concretely enough that a result could actually fail to support it — "nothing happens" is rarely a real null hypothesis on its own; say what "nothing happens" would look like in the actual metric.

## Motivation

Why does this question matter for the project's research thesis (`../README.md`)? What would change if it were answered either way?

## Experimental design

How will this be tested? System(s), topology, fault model, dataset, query workload, what varies and what's held constant.

## Metrics

What is actually measured, and which of those measurements decide the outcome.

## Instrument characterization

*(Required for any confirmatory sweep. Fill in from existing artifacts before
spending compute — archived runs are usually enough.)*

Pre-registration protects the question from the answer; it does not protect
the measurement from the apparatus. Three consecutive confirmatory sweeps in
this project failed their own validity preconditions after the fact, and in
each case the failing quantity was measurable beforehand from data already in
the repo: the corpus was un-indexed for the whole measurement window
(`qdrant_optimizer_masking/`, correcting `cross_system_replication/`), the
chaos window was an unnoticed function of the variable being varied
(`experiment/qdrant-kill-spacing`, PR #20), and the probe was too slow to
resolve the signal it was measuring (`kill_spacing_corrected/`, PR #25 —
which derived all three of its own amendments from #9's archived runs at zero
cost, and then still missed the fourth). State, with the run that measured it:

- **Sampling interval, realized** — not the requested `--sample-interval`, the
  measured one, and what dominates it (probe cost, scoring cost, corpus size).
- **Signal lag and duration** — how long after the treatment the measured
  quantity moves, and for how long it stays moved (e.g. damage appears a
  median 14.1s after a kill, range 7.5–46.8s; episodes last N samples).
- **The ratio** — samples per signal episode at the interval above. Say the
  minimum you need and why. If the ratio is unknown, the sweep is a pilot,
  not a confirmatory run, and the Decision section must say so.
- **What the instrument is actually measuring** — if a metric is computed by
  the system under test (an index recall, a count), what state must hold for
  it to mean what the Metrics section says it means, and how that state is
  confirmed during the run.

## Baselines / controls

What is this compared against? Note if a no-fault / no-treatment baseline is required to establish a noise floor before the treatment condition means anything (as it was for `replica_recall/`'s chaos-vs-baseline design).

## Expected outcomes

Enumerate the plausible outcomes, not just the hoped-for one — e.g. "(a) effect present and detectable, (b) effect present but below detection threshold, (c) no effect, (d) effect present but confounded by X."

## Interpretation plan

For each outcome in the previous section: what would it mean, and what would it *not* mean? What follow-up would each outcome imply?

## Confounds considered

What could produce a false positive or false negative here, and how (if at all) is it controlled for?

---

## Results

*(Filled in after the experiment runs — this section, and everything below it, does not exist when the spec is first committed.)*

## Interpretation

What did the result actually establish? What does it explicitly not establish?

## Decision

MERGE / ARCHIVE / REVISE / ABANDON / REPRODUCE — and why. See `../GIT_WORKFLOW.md`'s merge criteria before deciding.
