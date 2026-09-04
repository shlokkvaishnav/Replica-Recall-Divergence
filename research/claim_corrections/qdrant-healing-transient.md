# Spec: analysis/qdrant-healing-thesis

**Branch:** `analysis/qdrant-healing-thesis`
**Issue:** #36 (body copied verbatim below)
**Date opened:** 2026-09-04
**Status:** COMPLETE

### Type

analysis (analysis of an existing result, no new data collection)

### Research question

What does the healing result (`research/qdrant_index_recall_healing/`, PR for #35) change in the top-level README's claim framing and open question #1, and what does `DECISION_LOG` need to record about the excluded seed and the gated protocol's duration limit?

### Hypothesis

Not a test. The claims to write: (1) on Qdrant, the replica-level `index_recall` loss under node-kill chaos is a transient of the restart — inside the seed's own baseline range over the last 60s of a 180s quiesce in 4 of 4 judged seeds (one of five unmeasured: zero kills), with the one visible post-kill loss gone within 30s; (2) completeness recovered 100% in 4/4 at 180s where #6's 50s window saw 0–100% — a horizon effect, hypothesis-level; (3) the Qdrant leg of open question #1 is closed in both directions on both axes; the remaining step is Weaviate.

### Null / alternative hypothesis

N/A. Wording must fail these before it lands: it must not say "heals" without the horizon and the 4-of-5; it must not promote the completeness horizon effect above HYPOTHESIS; it must carry the noise-floor caveat on the closest seed (passes by 0.0002); and the ESTABLISHED contrast with nano-db ("has not returned in any observed post-recovery window") must state that the two systems were observed on different horizons and axes.

### Motivation

`GIT_WORKFLOW.md` asks every PR whether it changes the thesis; #35's PR answers yes and leaves the README/DECISION_LOG edit to its own diff per the isolation rule — as #31 did with #32. Without it the README's HYPOTHESIS line ("does not fully heal … a hint") outlives the measurement that answered it.

### Experimental design

No runs. Edit `README.md`: HYPOTHESIS — replace the healing hint with the result; ESTABLISHED — add the transient sentence with horizon, unit, n; DO NOT CLAIM — "Qdrant's graph damage persists" becomes "…persists beyond one 30s bin" as not supported, and add "healing seed-inconsistent on Qdrant" as retired at 180s (hypothesis-level); open question #1 — Qdrant leg closed, Weaviate named as the step; Limitations — the gated protocol's ~120s duration limit at 1,000 KB. `research/README.md` cross-system row; `research/cross_system_replication/SPEC.md` a dated addendum on healing (its own 50s result was a horizon effect); `research/DECISION_LOG.md` — why one seed was excluded by a rule written when the case appeared, why the closest seed is reported as a tie, and the duration limit. Spec in `research/claim_corrections/`, as #32's was.

### Metrics

N/A. Review checks every sentence against `research/qdrant_index_recall_healing/results/analysis_output.txt` and `aggregate_output.txt`.

### Baselines / controls

#32's PR (#33) is the precedent for the shape and the review bar.

### Expected outcomes

The README changes; nothing else does.

### Interpretation plan

N/A.

### Confounds considered

Overstatement toward the tidy sentence ("Qdrant heals"). The tidy sentence is true only with: at the replica level, within 180s, in 4 of 5 seeds with one unmeasured, at k = 10 over 100k on one host, on a metric with ~1% of headroom.

### Before submitting

- [x] I checked README.md's "Open research questions" and research/DECISION_LOG.md and this isn't a duplicate or already-ruled-out question.
- [x] This is one answerable question, not a broad restatement of the whole research thesis.

