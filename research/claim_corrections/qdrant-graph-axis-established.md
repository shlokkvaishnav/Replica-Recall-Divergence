# Spec: analysis/qdrant-thesis-update

**Branch:** `analysis/qdrant-thesis-update` (stacked on `experiment/qdrant-gated-index-recall`, PR #31)
**Issue:** #32 (body copied verbatim below)
**Date opened:** 2026-09-04
**Status:** COMPLETE

### Type

analysis (analysis of an existing result, no new data collection)

### Research question

What does PR #31's result — replica-level `index_recall` divergence under node-kill chaos on Qdrant at p = 0.0079, with the six-replica mean at p = 0.31 — change in the top-level `README.md`'s ESTABLISHED / HYPOTHESIS / OPEN / DO-NOT-CLAIM framing and open question #1, and what does `DECISION_LOG.md` need to record about the unit of analysis?

### Hypothesis

Not a hypothesis test. The claim to be written: replica-level `index_recall` divergence under chaos is not specific to nano-db; on Qdrant it is measurable only at the replica level, and the cluster-wide mean hides it. Open question #1's graph-quality axis moves from "untested, not null" to established-at-the-replica-level, with the unit stated, and the next step becomes Weaviate (real anti-entropy) as the Motivation always said.

### Null / alternative hypothesis

N/A — but the wording must fail two tests before it lands: (i) it must not read as "Qdrant's graph diverges" without the unit, because at the cluster level the measurement is a null; (ii) it must not promote the partial-healing observation (2/5 seeds below baseline after 50s, 4–5 samples each) beyond HYPOTHESIS.

### Motivation

`GIT_WORKFLOW.md` asks every PR whether it changes the thesis; #31 answered yes and, per the isolation rule, left the README/DECISION_LOG edit for its own diff. This issue is that diff. Without it the finding exists only in a subdirectory and the README still says "untested."

### Experimental design

No runs. Edit `README.md` (ESTABLISHED box: add the Qdrant replica-level result with unit and numbers; open question #1: rewrite to the Weaviate step and the healing follow-on; DO-NOT-CLAIM: add "cluster-wide `index_recall` divergence on Qdrant" and "mechanism of the per-replica loss"), `research/README.md`'s cross-system row, `research/cross_system_replication/SPEC.md` (a dated addendum pointing at #31, alongside the 2026-09-02 correction), and `research/DECISION_LOG.md` (why the unit is the replica; why the cluster mean is reported beside it, not instead of it; that #6's null had two causes). Same rules as `research/claim_corrections/`: original text struck, not deleted.

### Metrics

N/A. Review checks each new sentence against the numbers in `research/qdrant_gated_index_recall/SPEC.md` and against `RELATED_WORK.md`'s do-not-claim list.

### Baselines / controls

The 2026-09-02 correction addendum is the precedent for how a cross-system sentence is changed here.

### Expected outcomes

The README changes; nothing else does.

### Interpretation plan

N/A.

### Confounds considered

Overstatement, in the direction this project keeps finding in itself: "established" must carry "at the replica level, on one host, k = 10, 100k SIFT, ~1.2 points on the seed mean." Three review rounds were needed to get PR #12's README wording bounded; budget for the same.

### Before submitting

- [x] I checked README.md's "Open research questions" and research/DECISION_LOG.md and this isn't a duplicate or already-ruled-out question.
- [x] This is one answerable question, not a broad restatement of the whole research thesis.

