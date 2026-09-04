# claim_corrections

Corrections to claims this project had already published, each recording *why*
the wording was wrong rather than only what it now says.

These are not experiments. They collect no data and test no hypothesis, which is
why they live here rather than as `research/<experiment>/` directories — that
namespace means "an experiment lives here," and two documentation corrections
filed alongside real sweeps made the index harder to read, not richer.

| file | what was corrected |
|---|---|
| [`layer3-query-pinning.md`](layer3-query-pinning.md) | The README listed "does `loo_agreement` survive non-pinned queries?" as open after PR #7 had answered it. Narrowed to the axis still genuinely open — a different query *distribution*, not merely a non-pinned one. |
| [`qdrant-graph-quality-withdrawal.md`](qdrant-graph-quality-withdrawal.md) | The cross-system spec listed "Qdrant diverges in data completeness but not graph quality" under *establishes*. PR #11 showed that `index_recall` comparison ran over a corpus that was un-indexed for 60–84% of the measurement window, so it measured exact scans rather than a graph. Withdrawn in **both** directions. |
| [`qdrant-graph-axis-established.md`](qdrant-graph-axis-established.md) | The reverse direction of the row above, two days later: on a corpus gated HNSW-indexed (PR #29) and at the replica level, Qdrant's `index_recall` *does* diverge under chaos (PR #31, p = 0.0079) — while the cluster-wide mean does not (p = 0.31). README ESTABLISHED/HYPOTHESIS/DO-NOT-CLAIM and open question #1 rewritten with the unit in the claim; `DECISION_LOG` records why the unit is the replica. Not a correction of an error this time, but the same discipline: the sentence that would read best ("Qdrant's graph diverges under chaos") is the one that must not be written without its qualifier. |
| [`qdrant-healing-transient.md`](qdrant-healing-transient.md) | The README carried "the per-replica `index_recall` loss on Qdrant does not fully heal" as HYPOTHESIS, from a 50s quiesce window with 4-5 samples. At 180s it heals (PR #37), and the data axis recovers 100% where the same 50s window had shown 0-100%. Retired as a horizon effect — the withdrawn claim is struck, not deleted, and the replacement carries its own qualifiers (4 of 5 seeds, one unmeasured, 180s, one host, a 0.0002 margin on the closest seed). |

Each file keeps its review addenda, and those are the reason this directory is
worth preserving at all. The corrections themselves are short; what is not
recorded anywhere else is the pattern the review rounds exposed — **every
overstatement found ran in the same direction, toward a tidier and more
confident claim than the evidence supported, including one that appeared inside
a correction of an over-claim, in the clause asserting that something had been
checked rather than assumed.**

That is a standing bias in how results get written up, not a set of one-off
slips, and in every case the only thing that caught it was re-reading the raw
per-seed data instead of the prose describing it. It has held through four
entries: the most recent (2026-09-04, PR #37) was a summary sentence calling a
0.003 dip with n = 3 "0.001 with n = 1-2" — smaller, tidier, and wrong, in a
writeup whose own table said otherwise.
