# Spec: method/chaos-loop-timeout

**Branch:** `method/chaos-loop-timeout`
**Issue:** #38 (body copied verbatim below, per `AGENT_PIPELINE.md`)
**Date opened:** 2026-09-04
**Status:** IN PROGRESS

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

