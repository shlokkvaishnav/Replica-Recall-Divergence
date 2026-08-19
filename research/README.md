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

**Confirmatory** means the question and comparison design were fixed before the relevant run. **Exploratory** means the finding was noticed first and investigated after — reported as such rather than reframed as planned.

## Reproducing

See the top-level [README's reproduction section](../README.md#reproducing-the-experiment) for the short version, or [`replica_recall/README.md`](replica_recall/README.md) for full methodology, every design decision and why, and known limits.
