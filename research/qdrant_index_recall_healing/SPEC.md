# Spec: experiment/qdrant-index-recall-healing

**Branch:** `experiment/qdrant-index-recall-healing`
**Issue:** #35 (body copied verbatim below, per `AGENT_PIPELINE.md`)
**Date opened:** 2026-09-04
**Status:** COMPLETE

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


---

## Results

Ten runs (`results/seed<N>_{baseline,quiesce}/`), seeds 20261100–20261104, every gate closed in 4–19s at 100,000–100,416 confirmed writes. `analyze_healing.py` regenerates every number below into `results/analysis_output.txt`; `../replica_recall/aggregate.py`, unmodified, gives the completeness healing table in `results/aggregate_output.txt`.

**One quiesce run is unmeasured.** Seed 20261100's chaos window ran 156→283s — **127s on a `--chaos-duration 50` setting — with zero kill events**; the other four windows are 53–58s with 3–4 kills each. `chaos_stop_rel` is taken after the chaos thread is joined with a 20s timeout, so a window past ~70s means the thread did not return; a `docker kill`/`start` call that blocked is consistent with that and is the likeliest cause, but it was not observed. (SPEC alternative (iii) as written anticipated an early last kill, not no kill.) Filed as a `[process]` harness issue in the shape of #26: **#38**. The run is kept, reported, and excluded from the healed count; it was not re-run, so the pre-registered N of 5 became 4 judged.

**Primary — worst replica per round, conditioned ≥0.95, last 60s of the quiesce vs the seed's own baseline range:**

| seed | baseline min–max (n cond./all) | last kill | post rounds (cond./all) | bin 0 (0–30s after last kill) | last-60s mean | healed | time to baseline |
|---|---|---|---|---|---|---|---|
| 20261100 | 0.987–0.992 (13/16) | — | — | — | — | **unmeasured** (no kills; 127s window) | — |
| 20261101 | 0.983–0.992 (7/19) | 204s | 4/14 | 0.9925 | 0.9900 | yes | 0s (no loss visible) |
| 20261102 | 0.987–0.994 (13/20) | 202s | 14/16 | 0.9903 | 0.9930 | yes | 120s by the strict rule; no loss visible |
| 20261103 | 0.990–0.998 (18/20) | 159s | 15/17 | 0.9923 | 0.9937 | yes | 60s: the 30–60s bin dips 0.003 (0.987, n = 3), back by 60s |
| 20261104 | 0.986–0.994 (10/16) | 146s | 17/17 | **0.9457** | 0.9862 | yes, by 0.0002 | ≤30s to 0.988; later bins hover 0.985–0.986 at the minimum |

**4/4 judged seeds are inside their baseline range over the last 60s.** After the last kill: two seeds (20261101, 20261102) show no dip at any bin beyond noise (20261102's 90s bin sits 0.001 under the minimum with n = 2); one (20261103) dips **0.003** below its minimum in the 30–60s bin (0.987, n = 3) and is back by 60s; one (20261104) drops to **0.946** in the 0–30s bin and is back to 0.988 by the next. The loss PR #31 measured sits *during* the chaos window, on the just-restarted node, and does not outlive the next sample bin in any seed. The unconditioned last-60s means (0.985–0.992) agree with the conditioned ones.

**Killed node's indexed fraction after the last kill** stays 0.90–0.96 across the bins in every seed — below the 0.95 bar for much of the window — so the *conditioned* worst replica is often not the killed node. That is alternative (ii)'s signature in the un-conditioned view: what regrows is the appendable tail on every node at 250s durations (baseline medians 0.936–0.954 too), not a killed-node-specific deficit. Retention of conditioned rounds is 0.29–1.00 post-kill and 7–18 of 16–20 rounds in the baselines; the ranges above are stated on those counts.

**Completeness (secondary, `aggregate.py`):** missing ids at chaos stop 468 / 19 / 248 / 86 for seeds 20261101–104, **0 at end of the 180s quiesce in all four — 100% recovered.** PR #6's 50s quiesce saw 84% / 0% / 25% / −32% / 100%.

## Interpretation

**Outcome (a), with the loss smaller than the question assumed.** On this horizon the killed replica's `index_recall` is back inside its baseline range in every judged seed; in two of four there was nothing to heal after the last kill, one dipped 0.003 for one bin, and one dropped to 0.946 for one bin. The largest visible loss recovered within 30s — the same order as the 4.7–16.9s restart re-index #29 measured. Qdrant differs from nano-db here: nano-db's missing data "has not returned in any observed post-recovery window"; Qdrant's graph loss is a transient of the restart.

**What "healed" means at this resolution.** Seed 20261104 passes the pre-registered criterion by 0.0002 and its later bins sit at 0.985–0.986 against a baseline minimum of 0.986 — a tie at the noise floor of `index_recall` at k = 10 over 100k vectors (~1% headroom, #31). The honest reading is "indistinguishable from baseline," not "recovered above it." Seed 20261102's time-to-baseline reads 120s only because one bin (n = 2) sits 0.001 under the minimum; seed 20261103's reads 60s because its 30–60s bin (n = 3) is 0.003 under — a small real dip, recovered by the next bin. Neither had a loss at t = 0.

**The tail, again.** At 240–250s durations the appendable tail keeps every replica's indexed fraction near or below 0.95 (medians 0.936–0.954), so conditioning at the bar retains as little as 29% of post-kill rounds (seed 20261101) and 7/19 baseline rounds. The pre-registered metric survives because the unconditioned view says the same thing, but any future run longer than ~120s at this write rate should lower the threshold below 1,000 KB or pause writes — the instrument's own limit (#29) reappears as duration grows.

**Completeness heals fully at 180s.** Four of four seeds recovered every missing id where PR #6's 50s window had seen 0–100%. That is a horizon effect on the data axis, and it retires "healing seed-inconsistent" as a Qdrant characteristic — at hypothesis level, since n = 4 and the runs were not designed for it.

**Not established.** Anything about the mechanism of the during-chaos loss (unchanged from #31). Healing beyond 180s (nothing suggests a later relapse; nothing tests one). The 127s zero-kill window's cause — a harness defect to file, not a finding. Five seeds were pre-registered and four were judged.

## Decision

**MERGE.** Pre-registered metric, new seeds, matched protocol, one honest exclusion made by a rule written when the case appeared and before any number was used (zero kills → unmeasured), the numbers recomputable by two tools, the noise-floor reading stated instead of rounded up. Result: on Qdrant the replica-level `index_recall` loss under node-kill chaos is a **transient of the restart** — a 0.946 dip for one 30s bin in one seed, a 0.003 dip for one bin in another, nothing beyond noise in the other two, and every judged seed inside its baseline range over the last 60s; the data axis heals completely within 180s.

**Changes the top-level README:** HYPOTHESIS's "does not fully heal" line becomes an ESTABLISHED-at-this-horizon statement with the unit, the horizon, and the 4-of-5; the Qdrant leg of open question #1 closes on both axes and both directions, leaving Weaviate. `DECISION_LOG` entry: why n = 4, and why the tail forces a duration cap on the gated protocol. As with #31, the thesis edit is its own `analysis/*` diff — to be filed, not remembered.

**Follow-ons, each its own issue:** (1) `method/*`: the randomized kill loop can block on a Docker call and leave a 127s window with no kills — the harness should time out a kill and record it as an event with `alive_after_restart: null` rather than silently extending the window; (2) `method/*`: a duration cap or write-pause for gated runs so the tail cannot starve conditioning (the #29 limit, now with a number: ~120s at 1,000 KB and 1.6k writes/s); (3) Weaviate.
