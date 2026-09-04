# Spec: method/chaos-loop-timeout

**Branch:** `method/chaos-loop-timeout`
**Issue:** #38 (body copied verbatim below, per `AGENT_PIPELINE.md`)
**Date opened:** 2026-09-04
**Status:** COMPLETE

### Type

method (a new methodological component — metric, detector, protocol)

### Research question

Not a research question — a harness defect in the shape of #26. `qdrant_run_experiment.py`'s randomized chaos loop can leave a run with **no kill events in a chaos window that ran far longer than requested**, and nothing in the run's output says so except an empty `events.json` and a `chaos_stop_rel` that does not match `--chaos-duration`.

### Hypothesis

Observed once (PR for #35, seed 20261100 quiesce): `--chaos-duration 50`, `chaos_start_rel` 156s, `chaos_stop_rel` 283s — a **127s** window — with `events.json == []`. The other four quiesce runs in the same sweep had 53–58s windows and 3–4 kills. `chaos_stop_rel` is recorded after `ct.join(timeout=20.0)`, so a window past ~70s means the chaos thread did not return on time; a `docker kill` or `docker start` call that blocked is consistent with that and the likeliest cause, but it was not observed — the loop records events only after a kill *completes*, so a blocked call leaves no trace.

### Null / alternative hypothesis

N/A. The defect is that the record is silent; the fix is to make it loud.

### Motivation

A run like this looks like a healthy quiesce run to every downstream tool (`aggregate.py` skipped it silently because it had nothing to heal; `analyze_healing.py` would have scored it "healed" until a zero-kill rule was added mid-sweep). It cost one of five pre-registered seeds in #35 and would have cost the conclusion if the rule had not been added before the numbers were used. Every future chaos run on this harness is exposed.

### Experimental design

Harness-only, on `research/cross_system_replication/qdrant_run_experiment.py` and `qdrant_docker_harness.py`:

1. Wrap each `docker kill` / `docker start` in the randomized loop with a timeout (e.g. 30s); on timeout, append an event with `alive_after_restart: null`, `timed_out: true`, and the elapsed time, and continue or abort per a flag.
2. Record `chaos_requested_s` and `chaos_realized_s` in `run_meta.json`; if realized exceeds requested by more than the join timeout, set `chaos_window_overrun: true`.
3. `run_meta.json` gains `kill_count`; a chaos or quiesce run with `kill_count == 0` prints a FATAL-style warning at the end and sets `chaos_no_kills: true`, so a sweep driver can refuse it the way `qdrant_sweep.py` refuses a missing `samples.csv`.
4. Validation: one no-cluster test with a fake container whose `kill()` blocks, following `qdrant_kill_scheduler/test_kill_schedule.py`'s pattern; one live run at #35's parameters confirming normal runs are unchanged.

### Metrics

N/A — pass/fail on the test, plus a diff of `run_meta.json` before/after on a normal run showing only the new fields.

### Baselines / controls

Seed 20261100's quiesce run in `research/qdrant_index_recall_healing/results/` is the case to reproduce against (a blocked call cannot be reproduced on demand; the fake-container test stands in).

### Expected outcomes

The harness cannot again produce a chaos run with zero kills that reads as normal.

### Interpretation plan

N/A.

### Confounds considered

A timeout that is too short would turn slow-but-successful restarts (Qdrant's `docker start` costs ~3.3s, #19) into false timeouts; 30s is an order of magnitude above that.

### Before submitting

- [x] I checked README.md's "Open research questions" and research/DECISION_LOG.md and this isn't a duplicate or already-ruled-out question.
- [x] This is one answerable question, not a broad restatement of the whole research thesis.


---

## Results

**Root cause, more specific than this issue guessed.** The issue said "a `docker kill`/`start` call that blocked is consistent with [the 127s window]." It is narrower than that: `ManagedContainer._docker` already runs `subprocess.run(..., timeout=30)`, so a hung daemon does **not** block forever — it raises `subprocess.TimeoutExpired`. Both chaos loops run in **daemon threads** with no exception handling, so that raise ends the loop immediately and silently: no event, no log line, no non-zero exit. The run then completes normally. `chaos_stop_rel` is recorded when the phase timer ends and the container-revival loop finishes, which is why the window read 127s while `events.json` was empty. The proposed fix (add a timeout) was already half-present; what was missing was catching what the timeout raises.

**Changes.**

1. `chaos_loop` and `chaos_loop_scheduled` wrap each kill/restart in `try/except Exception`, append an event with `failed: true`, `error`, `timed_out` (true iff `TimeoutExpired`), `alive_after_restart: null`, print a `[chaos] FAILED …` line, and **continue to the next kill**. The scheduled loop's failed events keep their full provenance (`condition`, `seq`, `requested_at_s`, `realized_at_s`, `requested_gap_s`, `realized_gap_s`).
2. `run_meta.json` gains `kill_count` (completed kills), `kill_failures`, `chaos_no_kills`, `chaos_requested_s`, `chaos_realized_s`. Successful events gain `failed: false`, so old and new records are distinguishable.
3. The runner prints a WARNING to stderr when chaos was requested and no kill completed.
4. `qdrant_sweep.py` refuses such a run — `FAILED (chaos requested but no kill completed; N failed attempt(s), window Xs vs requested Ys -- issue #38)` — the same way it refuses a missing `samples.csv`. **`json` was not imported in that file**, so the guard as first written would have raised `NameError` on the exact path it guards; caught by importing it and asserting the symbol resolves.

**Validation.**

- `test_chaos_failures.py`, no Docker: **13/13**. Covers both loops against fake containers that raise `TimeoutExpired` and a plain `RuntimeError`; asserts the failed event's fields, that `timed_out` distinguishes the two, that **the loop survives and keeps killing** (the defect itself), that a later success is `failed: false`, that the scheduled loop's failed step keeps its provenance, and that neither thread is left running.
- `../qdrant_kill_scheduler/test_kill_schedule.py`: **27/27** unchanged — the modified loops still satisfy the scheduler's own checks.
- **Live run** (`--chaos-duration 40 --pre-chaos-s 15 --duration 90`, 60k vectors, seed 20261200): `kill_count: 3`, `kill_failures: 0`, `chaos_no_kills: false`, `chaos_requested_s: 40`, `chaos_realized_s: 40.929`, three events all `failed: false` with `alive_after_restart: true`. A healthy run is unchanged apart from the new fields.

**What this does not do.** It does not make a hung Docker daemon less likely, and it does not retro-diagnose seed 20261100 — that run predates the fix, so its `events.json` is empty either way and it stays excluded in `../qdrant_index_recall_healing/`. It does not add a timeout to the *sampler* or *validator* threads, which have the same daemon-thread shape and are the obvious next place to look; that is deliberately out of scope here rather than folded in.

## Decision

**MERGE.** The defect was a silent failure in the instrument that already cost one pre-registered seed; the fix makes it loud at three levels (event, `run_meta`, sweep refusal), is covered by tests that fail without it, and leaves healthy runs byte-identical apart from the new fields. Additive; no metric, dataset, or protocol changed.
