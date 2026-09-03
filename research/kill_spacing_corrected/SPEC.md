# Spec: experiment/kill-spacing-corrected-measure

**Branch:** `experiment/kill-spacing-corrected-measure`
**Date opened:** 2026-09-02
**Status:** COMPLETE - 15 runs done. **VOID by Amendment 4**: the sampling
precondition failed. The comparison is not reportable; what the runs do give is
a measured sampling floor and the reason it exists.

Issue: closes #24. Body copied verbatim below (heading levels normalized).

---

## Type

experiment (one specific, narrowly-scoped experiment)

## Research question

Does inter-kill spacing affect how much damage a Qdrant cluster accumulates under chaos — measured at a point where the damage still exists? #9's sweep could not answer this because its measurement point sat after a condition-dependent recovery window; with the measurement point corrected, do the three kill patterns (compressed same-node, spaced same-node, spread across nodes) differ?

## Hypothesis

Kills landing on a node before it has caught up accumulate damage that spaced or spread kills do not, because the node never reaches a settled state between faults. #9's archived data points this way but cannot establish it: peak missing ids ran 3,225–6,517 under compressed kills against 746–1,693 (spaced) and 175–1,386 (spread), and integrated damage-time agreed. Those measures were computed **after** seeing that the pre-registered metric was degenerate, so they are exploratory and are the thing this experiment exists to confirm or refute, not to cite.

Note what the data already suggests about the *mechanism*: spaced-same-node and spread were statistically indistinguishable from each other (p=0.55 peak, p=0.69 integrated) while both differed from compressed. If that replicates, the operative variable is recovery time between kills, not repetition against the same node — a sharper claim than #9's original framing.

## Null / alternative hypothesis

Null: with the measurement point corrected, the three conditions show no detectable difference in accumulated damage — #9's exploratory separation was an artifact of the differing quiet tails (the compressed condition had 91s of fault-free time inside its chaos window against 21s for the others), which inflated nothing but could plausibly have shaped what was measured. Alternative: compressed kills produce measurably more damage than either spaced or spread, with spaced and spread indistinguishable from each other.

## Motivation

#9 is archived unanswered (PR #20, `DECISION_LOG.md`). Its failure was not the hypothesis and not the instrument — both survived — but a single design decision recorded in its own spec: Amendment 3 held the chaos-window duration constant across conditions while varying spacing at fixed kill count. Those cannot both be held; the window is a function of the spacing. The compressed condition therefore stopped killing at t≈60s and idled for 91s inside its own chaos window, and Qdrant repaired everything before `heal_stats` took its reading at chaos-stop. All 15 runs reported zero damage: `recovered_frac` was 0/0, undefined rather than perfect.

That is a fixable measurement error, not a dead end, and the fix is cheap: the 15 runs, the validated scheduler (`research/qdrant_kill_scheduler/`), and the analysis tooling all already exist. Leaving it unfixed would strand a real, large, already-observed effect behind a known-bad measurement point — and #20's archive decision explicitly names this follow-up as what should happen next.

## Experimental design

Same scheduler, same three conditions, same paired-seed structure, same corpus scale as #9 (200k SIFT, so the corpus cannot exhaust mid-run — #9's Amendment 2, which worked: 0 void runs). One change, and only one:

**End the chaos window at the last kill plus a settling margin, rather than at a fixed duration.** This makes window duration co-vary with spacing, which is unavoidable at fixed kill count and is the *lesser* confound: an unequal window is a known, measurable difference between conditions, whereas a condition-dependent quiet tail silently hands one condition four times more free recovery than another, acting directly on the dependent variable.

State the invariant being held explicitly, since #9's mistake was holding the wrong one: **kill count, corpus, write rate, down-time and probe protocol are held constant; chaos-window duration is not and cannot be.**

Alternative worth considering in review before running: keep #9's fixed window and instead measure damage at `last_kill + settle_s`, leaving the protocol untouched and changing only the measurement point. This is cheaper and re-uses #9's committed runs directly, but it measures a different quantity (damage at a moment inside the window rather than at its end) and would need `heal_stats` to grow a second measurement mode. Whichever is chosen, it should be chosen and written down *before* the runs.

## Metrics

Primary, pre-registered here rather than chosen later: **integrated missing-id-seconds** over the chaos window — total damage-time, which is not inflated by kills overlapping in time. Secondary: **peak missing ids** at any instant, reported alongside precisely because it *is* sensitive to concurrency, so the two disagreeing would itself be informative. Tertiary: write-failure rate during chaos.

Absolute counts throughout, never the completeness ratio, per `DECISION_LOG.md`'s dilution-trap entry.

## Baselines / controls

`spread` is the built-in control: same kill count, same window, no same-node repeat. #9's 15 archived runs are the comparison for whether the corrected measurement point changes the picture — not a statistical baseline, since they measured a different thing, but a direct check that the only difference is the one intended.

A no-chaos run per seed establishes the noise floor for both damage measures under this protocol, which #9 did not collect and should have.

## Expected outcomes

(a) Compressed kills show measurably more accumulated damage; spaced and spread indistinguishable — confirms #9's exploratory observation and sharpens the mechanism to "recovery time, not repetition." (b) No detectable difference — the exploratory separation was an artifact of the quiet tail, and #9's hypothesis is refuted rather than merely unmeasured, which is a genuinely useful negative result. (c) All three differ, including spaced from spread — repetition against the same node matters independently of spacing, contradicting the mechanism above and needing its own follow-up. (d) The corrected window makes the conditions too short to accumulate measurable damage at all (the compressed condition's window would be ~25s), in which case kill count must rise before anything is comparable — a feasibility failure, and one to check on the first run rather than after fifteen.

## Interpretation plan

(a) is written up as a confirmed finding, scoped to Qdrant, this fault model and this scale, and it closes #9's question. (b) and (c) are recorded as findings in their own right; (c) additionally invalidates the mechanism sketch above and should say so. (d) is a design failure, not a result, and returns here for re-scoping before any further compute. In all four cases `DECISION_LOG.md` gets an entry, and #9's archived branch is cross-referenced so the two are readable as one thread.

## Confounds considered

**Unequal chaos-window duration** — accepted deliberately, stated as the lesser of two confounds, and quantified per run rather than assumed away. **Integrated damage-time is duration-sensitive**: a longer window has more time to accumulate damage-seconds, which biases *against* the compressed condition (the shortest window), so an effect surviving in that direction is conservative; normalising per unit window is worth reporting alongside the raw figure. **Restart latency**: `docker start` costs a near-constant +3.1–3.6s that is subtracted from every requested gap, so conditions are defined on realized spacing recorded per run, per #9's Amendment 1 and PR #18's validation. **Post-hoc metric risk**: the primary metric is fixed in this issue before any run — if it comes back degenerate again, that is a void result to record, not a licence to go looking for a metric that separates.


---

## Amendment 1: do NOT end the chaos window at the last kill - this issue's own proposal was wrong

#24's Experimental design proposed ending the chaos window at the last kill plus
a settling margin. Checked against #9's archived runs before committing any
compute, that would have **destroyed the signal it was meant to capture**.

Damage does not appear while a node is down. It appears *after* it returns:

| | measured across #9's 15 archived runs |
|---|---|
| kills | 45 |
| samples showing any damage | 31 |
| **lag, kill -> first visible damage** | **median 14.1s**, range 7.5-46.8s |

A window ending at `last_kill + settle_s` (3s) would close 4-44 seconds before
the damage from that kill became visible. #9 measured zero damage at chaos-stop
and this issue diagnosed that as a quiet tail; the deeper reason is that the
damage signal is *lagged*, and the proposed fix would have cut it off at the
front instead of diluting it at the back.

**Corrected design:** keep a fixed chaos window, and extend observation to at
least **60s past the last kill** - comfortably beyond the 46.8s worst observed
lag. Unequal window duration is avoided rather than accepted, because the
measure below is duration-normalized.

## Amendment 2: the phenomenon is under-sampled, and that is fixable for free

The damage signal is a brief spike, and #9 sampled it roughly once per episode:

| | measured |
|---|---|
| damage spikes observed per kill | **0.69** |
| samples showing damage | 31 of 305 (10.2%) |
| effective sample interval | **10.9s median** |
| episode duration | under one sample interval (never two consecutive damaged samples) |

So roughly a third of kills produced no observed damage at all, and where damage
was seen its magnitude is whatever a single sample happened to catch - not a
peak. **Any comparison of peak or integrated damage across conditions in #9's
data is dominated by sampling alignment**, which is the honest reason its
exploratory separation cannot be promoted, over and above it being post-hoc.

The fix costs nothing. A sample round costs `probe_s` (median 3.31s) +
`score_s` (median 1.42s), and `probe_s` is dominated by running `--queries 100`
searches against every replica. **This experiment's metric does not use queries
at all**: `completeness` is computed from `ListLocalIds` set differences, not
from retrieval. Dropping to `--queries 10` collapses the probe cost without
touching the measured quantity.

Target: effective sample interval **<= 4s**, giving >= 3 samples inside an
episode instead of <= 1. This is a **validity precondition**, not an aspiration:
the realized interval is computed per run, and if the median exceeds 4s the run
is void and re-run, exactly as #9 treated corpus exhaustion.

## Amendment 3: pre-registered parameters and metric

Held constant across conditions: corpus (SIFT, 200k, so it cannot exhaust -
#9's Amendment 2, which worked: 0 void runs), writers, kill count (3), fixed
down-time, probe protocol, chaos-window duration, quiesce length, pinned image.

```
--dist sift --sift-vectors 200000 --writers 4 --queries 10 --k 10
--warmup-s 15 --pre-chaos-s 25 --chaos-duration 110 --duration 260
--sample-interval 1 --kill-schedule <condition> --kill-count 3
```

`--duration 260` leaves ~125s of observation after the last long-gap kill,
against the 46.8s worst observed lag. `--sample-interval 1` plus the ~1s probe
cost at 10 queries should realize <= 4s; whether it does is checked, not assumed.

Seeds 20260950-20260954, the same five per condition, paired.

**Primary metric, fixed here before any run: mean missing ids over the
observation window** (integrated missing-id-seconds divided by window duration).
Duration-normalized on purpose, so it cannot be inflated by one condition being
observed longer. **Secondary:** peak missing at any instant, and the count of
samples showing any damage - the latter is a direct check on whether Amendment 2
worked.

**Statistics:** exact two-sided Mann-Whitney per condition pair, 5v5, floor
p=0.0079, per project convention, with per-seed values and effect sizes reported
alongside rather than instead.

## Amendment 4: what would make this uninterpretable

- **Realized sample interval > 4s median in any condition.** Void, re-run. The
  whole point is resolving the transient.
- **Fewer than 2 damage spikes per kill on average.** Then the phenomenon is
  still under-sampled and no comparison should be drawn, however tempting the
  numbers look.
- **Any condition showing zero damage across all 5 seeds** - void for that
  comparison, not "healed perfectly", per #9's lesson.
- **Realized short/long gaps overlapping**, contaminating condition assignment.

If the primary metric comes back degenerate again, that is a void result to
record - not a licence to search for a metric that separates. #9's exploratory
peak/integrated measures exist and will be tempting; they are secondary here
precisely so that temptation is settled in advance.

---

## Results

15 runs, 5 per condition, seeds 20260950-20260954. Raw data in
`results/<condition>_seed<seed>/`, analysis in `results/analysis_output.txt`.

### What the amendments fixed

Amendments 1-3 all worked, and measurably:

| | #9 | this sweep |
|---|---|---|
| corpus exhausted | 0/15 | 0/15 |
| `killed_while_down` | 0 | 0 |
| short/long realized gaps | separated | separated (0.18-2.08s vs 31.34-37.93s) |
| `probe_s` median | 3.31s | **1.14s** (`--queries` 100 -> 10) |
| effective sample interval | 10.9s | **4.27s** |
| damage spikes per kill | 0.69 | **2.29** |
| samples per damage episode | 1 (never two consecutive) | **2.45 mean, max 7** |

Amendment 1's premise held: damage is lagged, and observing 60s+ past the last
kill captured episodes that #9's protocol and #24's original proposal would both
have missed. Amendment 2's mechanism held too -- `completeness` comes from
`ListLocalIds`, not from queries, so cutting queries bought a 3x cheaper probe at
no cost to the measured quantity.

### Why it is void anyway

Amendment 4's first bullet: *realized sample interval > 4s median. Void, re-run.*

Median realized interval is **4.27s**, and 10 of 15 runs exceed 4.0s
(4.14-4.80s). The threshold was chosen as a proxy for ">= 3 samples inside an
episode", so the substantive goal was measured directly rather than argued
about: **16 of 42 episodes reached 3+ samples, and 11 of 42 are still single-
sample**. Both the proxy and the thing it stood for fail. This is not a
technicality to reason past -- a quarter of episodes are still measured by
whichever single sample happened to land on them, which is #9's defect at
reduced amplitude.

The spikes-per-kill precondition (>= 2.0) passed at 2.29. One precondition
passing does not rescue the other.

### The numbers, recorded because they exist, NOT reportable as a result

From void runs. They are written down so this branch is a complete record and so
nobody re-runs it expecting different bookkeeping - not as findings.

| metric | short-gap | long-gap | spread |
|---|---|---|---|
| **PRIMARY** mean missing (duration-normalized) | 185.3 | 128.3 | 148.7 |
| secondary peak missing | 3356.6 | 1693.4 | 1956.4 |

Primary: no pair separates (p = 0.55, 0.84, 0.69). Per-seed values overlap
heavily -- short-gap spans 71.0-296.7, spread spans 75.0-352.8.

Secondary peak: short vs long p=0.0317, short vs spread p=0.0952.

**That peak line is exactly what Amendment 4 predicted would be tempting**, and
it is refused on three independent grounds, any one of which is sufficient: the
runs are void; peak is a secondary metric; and peak is the same measure #9's
exploratory result used, which is why it was demoted to secondary here in
advance. The pre-registered primary metric found nothing.

### The floor, which is the useful finding

The interval did not miss its target by accident, and the cause is structural:

```
probe_s 1.14s + score_s 0.83s + 1s requested interval = ~2.97s   realized 4.27s
```

`probe_s` is no longer dominated by queries -- it is dominated by
`ListLocalIds`, which ships **81,775 ids per replica** across 6 replicas every
round. That cost scales with corpus size, and Amendment 2 of #9 requires the
corpus to stay large enough that writes are still in flight during chaos.

**So sampling resolution and signal availability are in direct tension, through
the probe.** A smaller corpus samples faster but stops producing damage to
measure (#9 proved that: 20k vectors, zero missing writes). A larger corpus
produces damage but cannot be sampled fast enough to resolve it. Turning
`--sample-interval` down further cannot fix this; the floor is the probe itself.

## Interpretation

**No answer to #24's question**, and the reason is now specific rather than
diffuse: the measurement is still too slow for the phenomenon, and it is too
slow for a reason that lowering the interval will not fix.

That is a materially better position than #9 left things. #9 could not say
whether its null was real or an artifact. This branch can say precisely what
would have to change: the probe needs a mode that fetches only what
`completeness` requires, without shipping every id on every replica every round
-- an id-set digest or a count-plus-checksum, rather than the full list. That is
a harness change, not a parameter change, and it belongs in its own `method/*`
branch.

Also worth carrying forward: the lag finding from Amendment 1 (median 14.1s,
range 7.5-46.8s) and the episode-duration data here are the first quantitative
description of the damage transient this project has. Any future design needs
them.

## Decision

**ARCHIVE the result, MERGE the branch** -- the same call as #9 and for a
related but distinct reason.

- The pre-registered comparison is void and the branch says so, rather than
  reporting the secondary peak result that would look like a finding.
- `analyze_corrected.py` is worth keeping: it evaluates and prints validity
  preconditions before any comparison, and it is what caught this. Unlike #9's
  analyzer, its comparison path did run on real damage -- 42 episodes across 15
  runs -- so it is exercised where it matters.
- The floor diagnosis and the transient characterisation are reusable, and are
  the reason a third attempt would be designed differently rather than just
  re-run.

Next, pre-registered separately: a cheaper completeness probe, then this
experiment again. Not another sweep at these parameters, which would reproduce
this outcome exactly.
