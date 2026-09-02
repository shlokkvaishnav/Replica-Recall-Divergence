# Spec: analysis/readme-layer3-pinning-caveat

**Branch:** `analysis/readme-layer3-pinning-caveat`
**Date opened:** 2026-09-02
**Status:** COMPLETE — outcome (a) with a piece of (b). See Results, below.

Issue: closes #10. Body copied verbatim below (per `research/AGENT_PIPELINE.md`'s implementer
instructions — this is the issue text unmodified, not a paraphrase).

---

## Type

analysis (analysis of an existing result, no new data collection)

## Research question

Now that PR #7's merged 5-seed sweep (`research/loo_agreement_nonpinned_queries/`) has established that query pinning is not load-bearing for `loo_agreement`'s above-chance detection performance at the tested scale — three independent 5-seed conditions (pinned, nonpinned/100 queries, nonpinned/15 queries) all landed in the same 0.81–0.87 hit-rate band with all 9 pairwise comparisons non-significant (p range 0.15–0.90) — does the top-level `README.md`'s current framing of the Layer 3 hypothesis and its DO-NOT-CLAIM section still accurately reflect what has and hasn't been shown, or does it need to be narrowed to incorporate this result?

## Hypothesis

The current README text (open research question #5, "Detector robustness — does `loo_agreement` still work against non-pinned, realistic query traffic?") is now answered for the specific condition tested (SIFT1M, this topology, query counts of 15/100) and should be updated to state that pinning was tested and found not to matter at this scale, rather than left listed as an open question — leaving it phrased as open after the evidence exists risks the exact kind of stale-claim drift `DECISION_LOG.md` already tracks corrections for (e.g. the ClickHouse citation, the ground-truth-free → judge-free correction).

## Null / alternative hypothesis

Null: the existing README wording, read literally, is already consistent with the new result and requires no substantive change — a reviewer re-reading the current text against PR #7's finding would not find a contradiction or a stale claim. Alternative: the current wording (framed as an open question, and the DO-NOT-CLAIM section's implicit scope) is now either understated (doesn't credit a real, well-supported result) or a query-workload-realism caveat needs to be added/adjusted somewhere in the ESTABLISHED/HYPOTHESIS/OPEN framing to reflect that this specific robustness dimension has been tested, not merely reasoned about.

## Motivation

This is the third of the follow-on items flagged by the reviewer across the two merged PRs (#6, #7) as needing to not quietly disappear — specifically, PR #7's own SPEC.md Interpretation plan for its outcome (a) explicitly says this result "should narrow the top-level README's Layer 3 / DO-NOT-CLAIM caveat about query-workload realism," and the reviewer noted this was correctly deferred within the PR (the result wasn't in yet when that section was written) but flagged it as worth a small follow-on doc task so it isn't lost. This directly serves `GIT_WORKFLOW.md`'s documentation and research-integrity merge criteria applied at the project level, not just the branch level — the README is the durable, external-facing claim surface this whole pipeline exists to keep honest.

## Experimental design

No new experiment. This is a documentation-analysis task: read the current top-level `README.md` (particularly the "Current findings" ESTABLISHED/HYPOTHESIS/OPEN/DO NOT CLAIM section and the "Open research questions / next experiments" list, item #5) and `research/replica_recall/README.md`'s Layer 3 framing, against PR #7's merged SPEC.md and its three-condition sweep result, and produce a precise, minimal wording change (not a rewrite) that: (1) removes or updates item #5 from "Open research questions" now that it has an answer, (2) states the query-pinning-robustness result plainly in the appropriate findings section, scoped exactly as tested (SIFT1M, this topology, query counts 15–100, not a general claim about all query distributions or scales), and (3) checks whether DO-NOT-CLAIM needs a new line or an existing line adjusted (e.g. anything currently implying detection was *only* shown to work under an unrealistic pinned workload).

## Metrics

Not applicable in the usual statistical sense — the "outcome" here is a specific, reviewable diff to `README.md` (and possibly `research/replica_recall/README.md`) that a subsequent mini-peer-review pass (or the next researcher/reviewer cycle) can check for accuracy against the underlying merged evidence, the same way any other documentation change in this project's history has been checked (per `DECISION_LOG.md`'s pattern of recording corrections).

## Baselines / controls

The merged PR #7 SPEC.md and its `compare_conditions.py` output are the ground truth this doc update must match precisely — no new claim should be added that isn't directly supported by that data, and the existing scope caveats already established in the pipeline (single system, single implementation, SIFT1M-specific) should be carried forward unchanged, not loosened.

## Expected outcomes

(a) The needed change is a small, localized edit (move item #5 from "open" to a stated finding, add one sentence to DO-NOT-CLAIM if warranted) — the common case, matching how prior corrections in `DECISION_LOG.md` have been handled. (b) On closer reading, the existing ESTABLISHED/HYPOTHESIS structure doesn't cleanly have a place for a negative-but-useful robustness result, and the section itself needs a small structural adjustment (e.g. a new bullet under a "TESTED, not established as expected" framing) rather than just new prose — worth flagging as a slightly larger task than a one-line edit if so. (c) On review, the current wording turns out to already be accurate/appropriately scoped and no change is actually needed — a legitimate outcome, not a failure of this task, and should be closed with that explicit conclusion recorded rather than a change forced for its own sake.

## Interpretation plan

(a) and (b) both result in a concrete `README.md` (and possibly `research/replica_recall/README.md`) edit, which should itself get a `DECISION_LOG.md` entry per that document's own stated policy ("add an entry whenever a research decision is made... a metric changed... not just when something goes wrong") — this is exactly the kind of decision (a claim's scope changing based on new evidence) that document exists to record. (c) means this issue closes as ABANDON-with-reason (per `GIT_WORKFLOW.md`'s five decision outcomes) rather than silently — "checked, no change needed" is itself worth one line in the issue/PR trail so a future pass doesn't re-ask the same question.

## Confounds considered

Scope creep risk: this task should touch only the wording directly related to the query-pinning-robustness finding, not become an opportunity to rewrite other parts of the README's findings section — that would violate the isolation spirit of `GIT_WORKFLOW.md` even though this is a docs-only change with no branch/experiment involved. Overclaiming risk in the other direction: the new wording must not imply `loo_agreement` robustness has been shown to generalize beyond the tested scale (SIFT1M, this topology, the specific query counts tested) — the existing project discipline (README's DO NOT CLAIM section) is exactly what this task needs to extend consistently, not undercut by overstating a now-more-favorable result.


---

## Results

The evidence this doc change had to match, read off PR #7's merged
`research/loo_agreement_nonpinned_queries/SPEC.md` (its two addenda) and
`results/aggregate_{pinned,nonpinned,nonpinned_small}.txt`:

| metric | pinned | nonpinned (100 q) | nonpinned_small (15 q) |
|---|---|---|---|
| detection hit rate | 0.870 ± 0.143 | 0.860 ± 0.143 | 0.811 ± 0.096 |
| rank correlation | 0.918 ± 0.087 | 0.826 ± 0.120 | 0.827 ± 0.127 |
| true-recall margin | 0.071 ± 0.020 | 0.057 ± 0.019 | 0.088 ± 0.038 |

Nine pairwise between-condition Mann-Whitney tests (3 conditions × 3 metrics),
none significant, p = 0.15–0.90. Chance baseline 1/3.

Three things in the existing text were checked against that:

1. **Open research question #5** ("does `loo_agreement` still work against
   non-pinned, realistic query traffic?") — answered for the tested axis, and
   still open for a different one. Rewritten, not deleted.
2. **The HYPOTHESIS box** — said nothing about query workload either way, so it
   was not *wrong*; it was silent about a robustness objection that has since
   been tested. Result added there rather than to ESTABLISHED (see below).
3. **The DO NOT CLAIM box** — contained no line that implied detection was only
   shown under a pinned workload, so nothing needed walking back. It did need a
   line added in the *other* direction, because removing #5 from the open list
   makes general workload robustness the default reading if nothing says
   otherwise.

Also checked and deliberately not changed: the "Current results" section's
statement that raw per-seed results are not committed. PR #7's sweep outputs
*are* committed, but that sentence is scoped to the Layer 1 chaos numbers
behind the ESTABLISHED box, which still are not — it remains literally true.

## Interpretation

Closest to **expected outcome (a)** — a small, localized edit — with one piece
of **(b)**: the ESTABLISHED/HYPOTHESIS/OPEN structure has no natural slot for
"a robustness objection to an unconfirmed hypothesis was tested and did not
hold." Promoting it to ESTABLISHED would be wrong (what is established is a
null between conditions, not the detector's claim); leaving it only in OPEN
would be wrong (it is not unresolved). It went into the HYPOTHESIS box as an
explicit "one objection tested, does not hold; this does not promote the
claim," which is the smallest change that keeps the box honest. No new
subsection was invented for it — a fourth category for one result would cost
more legibility than it buys, but the tension is real and is recorded here in
case a second such result ever arrives.

Scope was held to the query-pinning finding, per the issue's own confound note.
Two adjacent staleness items were found and deliberately **not** fixed here:

- `research/README.md`'s experiment index still lists cross-system replication
  as "**Not started** — spec committed, no implementation or results yet,"
  which PR #6 superseded. Belongs in its own change.
- Issue #9 (`stage:claimed`) has no branch and no PR.

Both are noted in the PR description as follow-ons rather than folded in.

## Decision

**MERGE** (self-assessed; the reviewer role decides independently). Docs-only,
no experimental claim added that PR #7's committed data does not support, every
new number traceable to that branch's addenda, and the scope caveats carried
forward unchanged rather than loosened. `DECISION_LOG.md` records the call per
its own stated policy.

## Addendum: review round 1 (2026-09-02)

The reviewer role checked the new wording against
`../loo_agreement_nonpinned_queries/results/compare_conditions_output.txt`
directly rather than against PR #7's addenda, and returned **REVISE** on three
overstatements. All three are fixed in the follow-up commit:

1. "all landed in the same 0.81-0.87 hit-rate band" described the three
   *condition means* (0.870 / 0.860 / 0.811) in language that reads as every
   run. The 15 per-seed hit rates actually span 0.652-1.000; 11 of 15 fall
   outside the quoted band. Now states the means as means, and cites the
   per-seed interleaving -- which is the more persuasive fact, and the one PR
   #7's own addendum leans on.
2. "the pinning and query-count axes are now closed" -- two query counts (100,
   15) are two data points, not a closed axis, and PR #7 says as much itself.
   Now "tested at 100 and 15 queries per round," with the axis explicitly not
   claimed as closed.
3. "both left detection accuracy unchanged" read a null as an equivalence. At
   5v5 the exact Mann-Whitney's floor is p=0.0079 and `DECISION_LOG.md` already
   records that this is a deliberately weak instrument, so the supportable claim
   is *no detectable difference at that power*, not *unchanged*. Fixed, and the
   same error in milder form ("removes a confound") was softened to "weakens
   that confound rather than eliminating it."

Worth recording as a finding about the process rather than about the text: all
three defects were in the direction of making the project's own result sound
cleaner, on a change whose entire purpose was to keep the claim surface honest.
The pull toward tidiness does not switch off just because the task is
anti-overclaiming.

The reviewer also flagged a gap in `AGENT_PIPELINE.md` -- its role-selection
queries have no condition matching a `stage:changes-requested` PR, so requested
revisions are invisible to the loop. Filed separately; not fixed here.
