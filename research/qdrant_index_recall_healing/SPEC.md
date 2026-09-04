# Spec: experiment/qdrant-index-recall-healing

**Branch:** `experiment/qdrant-index-recall-healing`
**Issue:** #35 (body copied verbatim below, per `AGENT_PIPELINE.md`)
**Date opened:** 2026-09-04
**Status:** IN PROGRESS

### Type

experiment (one specific, narrowly-scoped experiment)

### Research question

After node-kill chaos stops, does the killed replica's `index_recall` on Qdrant return to the no-chaos baseline range — and if so, how long does it take? PR #31 established the loss (worst replica 0.978 vs 0.990, p = 0.0079, killed node worst in 4/5 runs) and saw 2 of 5 seeds still below the baseline range across a 50s quiesce window with 4–5 samples each; README HYPOTHESIS carries that as a hint. This makes it a measurement.

### Hypothesis

The loss is transient on Qdrant: within 180s of the last kill, the worst replica's `index_recall` is back inside the baseline range in ≥4 of 5 seeds, because a restarted node reloads its persisted HNSW and re-indexes its appendable tail within 5–17s (#29) — leaving no obvious mechanism for a lasting deficit once the tail is indexed. This is the *opposite* of nano-db's established result ("missing data has not returned in any observed post-recovery window"), and the contrast is the point.

### Null / alternative hypothesis

(i) Persistent: in ≥2 of 5 seeds the worst replica's `index_recall`, averaged over the **last 60s** of a 180s quiesce, is still below the seed's own baseline minimum — the loss does not heal on this horizon. (ii) Healed-by-turnover, not repair: recovery coincides with the un-indexed tail being re-indexed (per-sample indexed fraction rising through 0.95) rather than with any change in an already-indexed segment — distinguishable because the samples carry their indexed fraction. (iii) Never damaged in the quiesce runs: kill timing is randomized, and a seed whose kills all land early may show no loss to heal — report time-since-last-kill, not time-since-chaos-stop.

### Motivation

README open question #1 names this as the next step after #31. The thesis hinges on "does it come back": on nano-db the data does not; on Qdrant the data axis was seed-inconsistent (#6) and the graph axis is now known to be damaged at the replica level. Whether Qdrant's graph damage is a blip or a state changes what "replication safety" means for a system with anti-entropy for data but not for graphs — which is the field-level claim in RELATED_WORK.

### Experimental design

PR #31's harness and parameters, changing only the quiesce window:

- Qdrant at the pinned digest, 2×3, `docker kill` randomized chaos, 4 writers, 100k SIFT (`--warmup-until-written 100000` on a 250k pool), `--indexing-threshold-kb 1000`, `--index-gate --index-gate-tol 0.05`, `--capture-telemetry`.
- 5 **new** seeds, 20261100–20261104. Two conditions per seed: `baseline` (`--no-chaos`, `--duration 240`) and `quiesce` (`--pre-chaos-s 20 --chaos-duration 50`, then **180s** quiesce → `--duration 250`). No separate `chaos` arm — #31 already established the loss; this asks about after.
- Driven through `qdrant_sweep.py --only baseline` / `--only quiesce` with flags forwarded, as #31 did; per-run `--out-dir` via the sweep tool's move.
- Analysis extends `research/qdrant_gated_index_recall/analyze_gated.py` (or a sibling) with post-chaos windows: worst replica per round, conditioned on that replica ≥0.95 indexed, in 30s bins after the last kill; plus **time-to-baseline**: first post-kill bin whose mean is ≥ the seed's baseline minimum and stays there.

**Instrument characterization** (from #29/#31 artifacts): realized sampling interval ~5s at 100k (`probe_s` 1.2–1.9s); restart re-index 4.7–16.9s; damage lag median 14.1s (#25). A 180s window at ~5s gives ~36 post-chaos samples per run against 4–5 in #31 — the quantity that was missing. Per-sample indexed fraction is recorded, so alternative (ii) is testable from the same data.

### Metrics

Primary, decides the outcome: for each quiesce seed, the worst replica's mean `index_recall` over the **last 60s** of the quiesce window (conditioned, as #31) compared to that seed's baseline range; count of seeds inside the range (≥4/5 = healed on this horizon). Secondary: time-to-baseline per seed (bins), the 30s-bin trajectory, `completeness` in the same windows for the data axis (continuity with #6's healing-variance result), and the indexed-fraction trajectory of the killed node.

### Baselines / controls

`baseline` at the same 240s duration is the range the recovery is judged against — 240s because the tail regrows during a long baseline (#29) and the range must reflect that, not a shorter, cleaner window. #31's quiesce runs (50s) are the prior.

### Expected outcomes

(a) ≥4/5 seeds back in range within 180s, time-to-baseline ≤60s: transient, and Qdrant differs from nano-db on healing. (b) ≥4/5 back in range but time-to-baseline >60s: heals slowly; report the distribution. (c) ≥2/5 still below range at 180s: persistent on this horizon — the nano-db shape. (d) Recovery tracks the indexed fraction crossing 0.95: the "loss" was the tail, and #31's conditioning at 5s resolution was too coarse — a method finding that revises #31's interpretation. (e) No seed shows a loss to heal: kill timing put the damage before the sampler saw it; report as unmeasured, not healed.

### Interpretation plan

(a)/(b) → README: the Qdrant graph loss is transient (with the horizon), the data-axis healing variance stands as the open Qdrant question; DECISION_LOG entry. (c) → README HYPOTHESIS "does not fully heal" becomes ESTABLISHED at this horizon with n; the mechanism question sharpens (what persists in a reloaded HNSW?). (d) → correct #31's Interpretation via `claim_corrections/`, and a `method/*` for finer indexed-fraction resolution. (e) → re-run with the #17 kill scheduler pinning the last kill, as its own amendment.

### Confounds considered

Writes continue through the quiesce, so the corpus at t+180s is ~300k larger than at the kill and the tail regrows — conditioning handles the tail, and the baseline is measured at the same duration for the same reason. A restarted node may also lag on *completeness*, which lowers the local ground-truth set and can raise `index_recall` spuriously — report `completeness` beside it and flag any seed where recovery coincides with completeness still <0.99. Randomized kills: report time from the *last* kill, and record each run's kill list. One host; five seeds; the 5-vs-5 floor applies to any test run.

### Before submitting

- [x] I checked README.md's "Open research questions" and research/DECISION_LOG.md and this isn't a duplicate or already-ruled-out question.
- [x] This is one answerable question, not a broad restatement of the whole research thesis.

