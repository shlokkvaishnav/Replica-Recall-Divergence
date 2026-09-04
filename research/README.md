# Research index

This directory is the research. `../cluster/`, `../include/`, `../src/`, `../proto/`, and `../tests/` are the experimental system it runs on — infrastructure, not the contribution.

## The contract

**Research problem.** Approximate (ANN) indexes under replication have no observable correctness criterion — a degraded replica returns plausible-looking but wrong answers, with no error signal any existing checker can name.

**Research question.** Does node-failure chaos cause measurable, replica-level search-quality divergence in a replicated HNSW-based vector database; does that divergence persist after full cluster recovery; and can a ground-truth-free peer-agreement signal detect the degraded replica?

**Scope.** Single system (nano-db), single implementation, SIFT1M (~200K vectors) + synthetic `lowdim`/`uniform` controls, 2×3 topology, SIGKILL-based failure only, 5 seeds.

Full contract, current claim, non-claims, and required next experiments: see the top-level [`README.md`](../README.md#current-findings). Full prior-art positioning and what NOT to claim because someone else already owns it: [`RELATED_WORK.md`](RELATED_WORK.md).

## Experiment index

| Investigation | Location | Type | Status |
|---|---|---|---|
| Original recall-measurement bugs | [`../docs/postmortems/recall-bugs.md`](../docs/postmortems/recall-bugs.md) | Exploratory | Closed — three bugs found and fixed, one residual effect (distance concentration on synthetic data) diagnosed as not-a-bug |
| Replica-recall divergence under chaos (Layer 1) | [`replica_recall/`](replica_recall/) | Confirmatory | Established on this system; see top-level README's ESTABLISHED box |
| Catastrophic single-replica disconnection | [`../docs/postmortems/catastrophic-disconnection.md`](../docs/postmortems/catastrophic-disconnection.md) | Exploratory | **Open** — two hypotheses ruled out, root cause unknown |
| Correctness criterion for replicated approximate indexes (Layer 2) | — | — | Not started |
| Production detection without ground truth (Layer 3) | `replica_recall/`'s `loo_agreement` detector | Confirmatory (in progress) | Hypothesis under test, not yet confirmed |
| Cross-system replication (Qdrant) | [`cross_system_replication/`](cross_system_replication/) | Confirmatory (pre-registered) | **Answered on both axes** — 5-seed Qdrant sweep: `completeness`/`e2e_recall` divergence established, healing seed-inconsistent. Graph-quality axis: first mis-measured over an un-indexed corpus (2026-09-02 correction), then re-measured gated-indexed in [`qdrant_gated_index_recall/`](qdrant_gated_index_recall/) — replica-level `index_recall` divergence p = 0.0079, cluster-level null p = 0.31, and that loss is a transient of the restart at a 180s horizon (2026-09-04 addenda). Remaining cross-system step: Weaviate |
| Why Qdrant's `index_recall` showed no divergence | [`qdrant_optimizer_masking/`](qdrant_optimizer_masking/) | Exploratory | Closed — not optimizer masking: the corpus was un-indexed for most or all of the measurement window, so `index_recall` was not measuring a graph |
| Detector robustness to non-pinned queries | [`loo_agreement_nonpinned_queries/`](loo_agreement_nonpinned_queries/) | Confirmatory (pre-registered) | Closed — clean null across three 5-seed conditions: query pinning is not load-bearing for `loo_agreement` at this scale. `--loo-query-mode` merged as infrastructure |
| Controlled kill-spacing scheduler (instrument) | [`qdrant_kill_scheduler/`](qdrant_kill_scheduler/) | Method | Merged — validated on a live cluster. Records realized vs requested spacing, because `docker start` costs a near-constant ~3.3s out of every requested gap |
| Does kill spacing explain Qdrant's healing variance? | branch [`experiment/qdrant-kill-spacing`](https://github.com/shlokkvaishnav/Replica-Recall-Divergence/tree/experiment/qdrant-kill-spacing) | Confirmatory (pre-registered) | **Archived, unanswered** — 15 runs completed but the pre-registered metric was degenerate (zero damage survived to the measurement point). Design error recorded; see PR #20 and `DECISION_LOG.md` |
| Does kill spacing affect accumulated damage? (corrected measurement) | [`kill_spacing_corrected/`](kill_spacing_corrected/) | Confirmatory (pre-registered) | **Closed — result VOID, line of work stopped** (PR #25). The pre-registered comparison failed its own sampling precondition (realized 4.27s vs ≤4.0s; 11 of 42 damage episodes single-sample). Merged for the methodology, not the result: first characterization of the damage transient (lag median 14.1s, range 7.5–46.8s) and of the probe-cost/corpus-size floor. No third kill-spacing sweep — see `DECISION_LOG.md` |
| Indexing gate so Qdrant's `index_recall` measures a graph (instrument for README priority #1) | [`qdrant_index_gate/`](qdrant_index_gate/) | Method (pre-registered, #28) | **Closed — instrument validated, 14-cell pilot run.** Gate closes only at `indexing_threshold` 1,000 KB (5/5; default 0/5, 5,000 KB 1/4 — replicas plateau at 85–93% because the appendable segment is never merged). Even then the window is ~93% indexed, not ≥95%; restarts recover in 5–17s. Follow-on: re-run the Qdrant sweep gated at 1,000 KB, reporting per-sample indexed fraction next to `index_recall` |
| Does chaos degrade Qdrant's `index_recall` on a gated (indexed) corpus? | [`qdrant_gated_index_recall/`](qdrant_gated_index_recall/) | Confirmatory (pre-registered, #30) | **Closed — outcome (a).** Worst-replica `index_recall` under chaos 0.978 vs baseline 0.990, every seed separated, p = 0.0079; the killed node is the worst replica in 4/5 runs. The six-replica mean does *not* separate (p = 0.31) — the effect is replica-level and averaging hides it, which with the un-indexed corpus is why PR #6 saw nothing. Run 0: exact search 1.000, HNSW 0.996–0.999 on the same state. Heals only partly in 50s (2/5 seeds below baseline after kills stop) — a follow-on, not a finding |
| Does the killed replica's `index_recall` on Qdrant heal after chaos stops? | [`qdrant_index_recall_healing/`](qdrant_index_recall_healing/) | Confirmatory (pre-registered, #35) | **Closed — outcome (a): a transient of the restart.** 180s quiesce, 4 of 4 judged seeds back inside their baseline range over the last 60s; after the last kill one seed dropped to 0.946 for one 30s bin, one dipped 0.003 for one bin, two showed nothing beyond noise, every dip gone by the next bin; the fifth seed is unmeasured (127s chaos window, zero kills — a harness defect, kept). Completeness recovered 100% in all four where #6's 50s window saw 0–100%. Closest seed passes by 0.0002, at the metric's noise floor — stated as such |
| Chaos loop dies silently on a hung Docker call (harness defect) | [`qdrant_chaos_loop_timeout/`](qdrant_chaos_loop_timeout/) | Method (#38) | **Closed — fixed and covered.** Root cause: the loops run in daemon threads, so the `TimeoutExpired` that `subprocess.run(timeout=30)` already raises ended them with no event, no log, and a normal-looking run (this cost one seed in #35). A failed kill is now an event with `failed`/`error`/`timed_out`; `run_meta` carries `kill_count`/`kill_failures`/`chaos_no_kills`/`chaos_realized_s`; `qdrant_sweep.py` refuses a chaos run with no kills. 13 new checks, the 27 scheduler checks still pass, verified on a live run |
| Claim corrections (documentation, no new data) | [`claim_corrections/`](claim_corrections/) | Analysis | Closed — two corrections to claims that had drifted from their evidence. No experiment; kept for the audit trail, and because each records *why* the wording was wrong |

**Confirmatory** means the question and comparison design were fixed before the relevant run. **Exploratory** means the finding was noticed first and investigated after — reported as such rather than reframed as planned.

## Reproducing

See the top-level [README's reproduction section](../README.md#reproducing-the-experiment) for the short version, or [`replica_recall/README.md`](replica_recall/README.md) for full methodology, every design decision and why, and known limits.

## Working on this research

Branches isolate uncertainty; `main` holds only validated state. Before starting a substantial new branch, copy [`SPEC_TEMPLATE.md`](SPEC_TEMPLATE.md) and fill in the research question, hypothesis, and design *before* implementing or looking at results. Full policy — branch naming, isolation rules, merge criteria, negative-results handling, commit conventions: [`GIT_WORKFLOW.md`](GIT_WORKFLOW.md). Why past decisions were made the way they were: [`DECISION_LOG.md`](DECISION_LOG.md). Running this as a researcher → implementer → reviewer pipeline across separate sessions, coordinated through GitHub issues/PRs/labels: [`AGENT_PIPELINE.md`](AGENT_PIPELINE.md).

**Note on the index above:** the cross-system row's status changed on 2026-09-02, after `qdrant_optimizer_masking/` showed the earlier "no graph-quality divergence on Qdrant" reading was an artifact of measuring an un-indexed corpus. The finding was withdrawn in both directions rather than reversed — that axis is now untested, not settled either way. See `DECISION_LOG.md`.

**Superseded pointer, kept for the record:** [`research/cross-system-replication`](https://github.com/shlokkvaishnav/Replica-Recall-Divergence/tree/research/cross-system-replication) — the pre-registered spec for testing whether the replica-recall divergence finding generalizes beyond this one system, per this index's Open research questions #1. No implementation or results yet; that's the point of committing the spec first.
